import subprocess as sp
import os
from pathlib import Path
from shutil import rmtree
import shutil
import hashlib
import gzip
import multiprocessing as mp
import signal
import datetime
import uuid




# lba size=17K
spart_firstlba='34'
# start=32K
spart_idbloader='start=64, size=960, type=8DA63339-0007-60C0-C436-083AC8230908, name="idbloader"'
# start=512K
spart_uboot='start=1024, size=6144, type=8DA63339-0007-60C0-C436-083AC8230908, name="uboot"'
spart_size_all=2048
spart_off_boot=4
spart_size_boot=256
skt_off_boot=spart_off_boot * 2048
skt_size_boot=spart_size_boot * 2048
# start=4M size=256M
spart_boot=f'start={skt_off_boot}, size={skt_size_boot}, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name="alarmboot"'
spart_off_root=spart_off_boot + spart_size_boot
spart_size_root=spart_size_all - 1 - spart_off_root
skt_off_root=spart_off_root * 2048
skt_size_root=spart_size_root * 2048
# start=(4+256)M=260M size=2048-1-260=1787M end=2048M
spart_root=f'start={skt_off_root}, size={skt_size_root}, type=B921B045-1DF0-41C3-AF44-4C6F280D3FAE, name="alarmroot"'



OUT_PATH = 'out'
RKLOADERS_PATH = 'rkloaders'
MIRROR_ARCHLINUXARM = 'http://mirror.archlinuxarm.org/aarch64/$repo'
MIRROR_VEXIONA='https://github.com/Vexiona/archrepo/releases/download/aarch64'
MIRROR_7JI='https://github.com/7Ji/archrepo/releases/download/aarch64'

build_id=f'ArchLinuxARM_aarch64_OrangePi5_{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}'
install_pkgs_bootstrap=('base', 'archlinuxarm-keyring', 'Vexiona-keyring', '7ji-keyring')
install_pkgs_normal=('vim', 'sudo', 'openssh', 'linux-firmware-orangepi-git', 'usb2host', 'python')
install_pkgs_kernel=('linux-aarch64-armbian-orangepi5-plus',)

def cleanup_parent():
    print('=> Cleaning up before exiting (parent)...')
    rmtree(project_path / 'cache' / 'root')
    for child in mp.active_children():
        child.terminate()

def check_identity_non_root():
    assert os.getuid() != 0, 'Not allowed to run as UID = 0'
    assert os.getgid() != 0, 'Not allowed to run as GID = 0'

def prepare_host_dirs():
    rmtree(project_path / 'cache', ignore_errors=True)
    (project_path / 'cache' / 'root').mkdir(parents=True)
    (project_path / 'out').mkdir(exist_ok=True)
    (project_path / 'pkg').mkdir(exist_ok=True)

def check_rkloaders():
    with open(project_path / RKLOADERS_PATH / 'sha512sums', 'r') as sums_file:
        sums_file_lines = sums_file.read().splitlines()
    sums_file_lines_split = sums_file_lines[0].split(' ')
    assert sums_file_lines_split[1] == 'list', 'List file checksum not found'
    with open(project_path / RKLOADERS_PATH / 'list', 'rb') as list_file:
        assert hashlib.file_digest(list_file, 'sha512').hexdigest() == sums_file_lines_split[0], 'List file checksum wrong'
    for rkloader_sum_line in sums_file_lines[1:]:
        rkloader_sum_line_split = rkloader_sum_line.split(' ')
        with open(project_path / RKLOADERS_PATH / rkloader_sum_line_split[1], 'rb') as rkloader:
            assert hashlib.file_digest(rkloader, 'sha512').hexdigest() == rkloader_sum_line_split[0], 'Rkloader file checksum wrong'

def prepare_pacman_static():
    return 0

def prepare_pacman_configs():
    # Create temporary pacman config
    pacman_config = '''[options]
RootDir      = cache/root
DBPath       = cache/root/var/lib/pacman/
CacheDir     = pkg/
LogFile      = cache/root/var/log/pacman.log
GPGDir       = cache/root/etc/pacman.d/gnupg/
HookDir      = cache/root/etc/pacman.d/hooks/
Architecture = aarch64
SigLevel     = {}''' + f'''
[core]
Server = {MIRROR_ARCHLINUXARM}
[extra]
Server = {MIRROR_ARCHLINUXARM}
[alarm]
Server = {MIRROR_ARCHLINUXARM}
[aur]
Server = {MIRROR_ARCHLINUXARM}
[Vexiona-aarch64]
Server = {MIRROR_VEXIONA}
[7Ji]
Server = {MIRROR_7JI}'''
    with open(project_path / 'cache' / 'pacman-loose.conf', 'w') as file:
        file.write(pacman_config.format('Never'))
    with open(project_path / 'cache' / 'pacman-strict.conf', 'w') as file:
        file.write(pacman_config.format('DatabaseOptional'))

def image_disk():
    output_base_img_path = project_path / OUT_PATH / f'{build_id}-base.img'
    output_base_img_path.unlink(missing_ok=True)
    with open(output_base_img_path, 'wb') as file:
        file.truncate(spart_size_all * 1024 * 1024)
    proc = sp.Popen(['sfdisk', output_base_img_path], stdin = sp.PIPE)
    proc.communicate(f'''label: gpt
{spart_boot}
{spart_root}'''.encode('ascii'))
    assert proc.wait() == 0
    assert sp.run(['dd', 'if=cache/boot.img', f'of={output_base_img_path}', 'bs=1M', f'seek={spart_off_boot}', 'conv=notrunc']).returncode == 0
    assert sp.run(['dd', 'if=cache/root.img', f'of={output_base_img_path}', 'bs=1M', f'seek={spart_off_root}', 'conv=notrunc']).returncode == 0
    os.sync()

def image_rkloader():
    table = f'''label: gpt
first-lba: {spart_firstlba}
{spart_idbloader}
{spart_uboot}
{spart_boot}
{spart_root}'''.encode('ascii')
    base_image_path = project_path / OUT_PATH / f'{build_id}-base.img'
    pattern_remove_overlay = ''
    pattern_set_overlay = ''
    for kernel in install_pkgs_kernel:
        pattern_remove_overlay+=f';/^\tFDTOVERLAYS\t{kernel}$/d'
        pattern_set_overlay+=f';s|^\tFDTOVERLAYS\t{kernel}$|\tFDTOVERLAYS\t/dtbs/{kernel}/rockchip/overlay/rk3588-ssd-sata0.dtbo|'
    with open(project_path / RKLOADERS_PATH / 'list', 'r') as list_file:
        list_lines = list_file.read().splitlines()
        for list_line in list_lines:
            list_line_split = list_line.split(':')
            rkloader_type = list_line_split[0]
            rkloader_config = list_line_split[1]
            rkloader_image_path = project_path / RKLOADERS_PATH / list_line_split[2]
            if rkloader_type != 'vendor':
                continue
            output_image_path = project_path / OUT_PATH / f'{build_id}_rkloader-{rkloader_config}.img'
            shutil.copy(base_image_path, output_image_path)
            with gzip.open(rkloader_image_path, 'rb') as rkloader:
                with open(output_image_path, 'r+b') as output_image:
                    shutil.copyfileobj(rkloader, output_image)
            proc = sp.Popen(['sfdisk', output_image_path], stdin = sp.PIPE)
            proc.communicate(table)
            assert proc.wait() == 0
            match rkloader_config.split('_', maxsplit=1)[1]:
                case '5b':
                    fdt='rk3588s-orangepi-5b.dtb'
                case '5_plus':
                    fdt='rk3588-orangepi-5-plus.dtb'
                case _:
                    fdt='rk3588s-orangepi-5.dtb'
            shutil.copy('cache/extlinux.conf', 'cache/extlinux.conf.temp')
            assert sp.run(['sed', '-i', f's|rk3588s-orangepi-5.dtb|{fdt}|', 'cache/extlinux.conf.temp']).returncode == 0
            if rkloader_config == '5_sata':
                assert sp.run(['sed', '-i', pattern_set_overlay, 'cache/extlinux.conf.temp']).returncode == 0
            else:
                assert sp.run(['sed', '-i', pattern_remove_overlay, 'cache/extlinux.conf.temp']).returncode == 0
            assert sp.run(['mcopy', '-oi', 'cache/boot.img', 'cache/extlinux.conf.temp', '::extlinux/extlinux.conf']).returncode == 0
            os.sync()
            assert sp.run(['dd', 'if=cache/boot.img', f'of={output_image_path}', 'bs=1M', 'seek=4', 'conv=notrunc']).returncode == 0

def release():
    return 0
    # rmtree(project_path / OUT_PATH / 'latest')
    # (project_path / OUT_PATH / 'latest').mkdir()
    # for suffix in suffixes:``
    #     name = f'{build_id}-{suffix}.gz'
    #     (project_path / OUT_PATH / 'latest' / name).symlink_to(f'../{name}')
        # gzip -9 out/"${build_id}-${suffix}" &
        # pids_gzip+=($!)

def spawn_and_wait():
    uuid_root = uuid.uuid4()
    uuid_boot = uuid.uuid4()
    args = ['unshare', '--user', '--pid', '--mount', '--fork']
    args.extend(['--map-user=0', '--map-group=0', '--map-users=auto', '--map-groups=auto'])
    args.extend(['/bin/bash', '-e', './build-arch-child.sh'])
    args.extend(['--uuid-root', str(uuid_root)])
    args.extend(['--uuid-boot', str(uuid_boot)])
    args.extend(['--build-id', build_id])
    for arg in install_pkgs_bootstrap:
        args.extend(['--install-bootstrap', arg])
    for arg in install_pkgs_normal:
        args.extend(['--install', arg])
    for arg in install_pkgs_kernel:
        args.extend(['--install-kernel', arg])
    assert sp.run(args).returncode == 0

def set_parts():
    return 0

def prepare_host():
    prepare_host_dirs()
    check_rkloaders()
    prepare_pacman_static()
    prepare_pacman_configs()

def cleanup_cache():
    rmtree(project_path / 'cache')

def main():
    # try:
        global project_path
        project_path = Path(__file__).parent
        signal.signal(signal.SIGINT, cleanup_parent)
        check_identity_non_root()
        prepare_host()
        spawn_and_wait()
        # The child should have prepared the following artifacts: cache/root.img cache/boot.img cache/extlinux.conf
        # And the child should have already finished out/*-root.tar
        set_parts()
        image_disk()
        image_rkloader()
        release()
        cleanup_cache()
    # finally:
        # return 0
        # cleanup_parent()

if __name__ == '__main__':
    main()
