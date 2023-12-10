import subprocess as sp
import os
from git import Repo
from pathlib import Path
from shutil import rmtree
import hashlib
import tarfile

REPOS = {
    'u-boot-vendor': {
        'url': 'https://github.com/orangepi-xunlong/u-boot-orangepi.git',
        'branch': 'v2017.09-rk3588',
        'board-configs': ('orangepi_5b', 'orangepi_5', 'orangepi_5_plus', 'orangepi_5_sata'),
        'gpt': b"""label: gpt
first-lba: 34
start=64, size=960, type=8DA63339-0007-60C0-C436-083AC8230908, name="idbloader"
start=1024, size=6144, type=8DA63339-0007-60C0-C436-083AC8230908, name="uboot"
"""
    },
    'u-boot-mainline': {
        'url': 'https://github.com/u-boot/u-boot',
        'branch': 'master',
        'board-configs': ('orangepi-5-plus-rk3588', 'orangepi-5-rk3588s'),
        'gpt': b"""label: gpt
first-lba: 64
start=64, size=32704, type=8DA63339-0007-60C0-C436-083AC8230908, name="uboot"
"""
    },
    'rkbin': {
        'url': 'https://github.com/armbian/rkbin.git',
        'branch': 'master'
    }
}

TOOLCHAIN_NAME = 'gcc-linaro-7.5.0-2019.12-x86_64_aarch64-linux-gnu'
TOOLCHAIN_URL = 'https://releases.linaro.org/components/toolchain/binaries/latest-7/aarch64-linux-gnu/'

RKLOADERS_PATH = 'rkloaders'

def update_repo(dir, url):
    try:
        repo = Repo(dir)
        print(f"Updating local git '{dir}' from '{url}'")
        repo.remote().fetch(prune=True) #update remote/origin
    except:
        rmtree(project_path / dir, ignore_errors=True)
        print(f"Cloning into '{dir} from '{url}")
        repo = Repo.clone_from(url, dir, mirror=True)

def update_all_repos():
    for repo, data in REPOS.items():
        update_repo(repo + '.git', data['url'])

def deploy_toolchain():
    if Path('toolchain').is_dir():
        print('Toolchain exists. Reusing')
        return
    print(f'Deploying toolchain {TOOLCHAIN_NAME}')
    rmtree(project_path / 'toolchain.temp', ignore_errors=True)
    (project_path / 'toolchain.temp').mkdir()
    assert sp.run(['wget', f'{TOOLCHAIN_URL}{TOOLCHAIN_NAME}.tar.xz', '-O', 'toolchain.tar.xz']).returncode == 0
    with tarfile.open('toolchain.tar.xz', 'r:xz') as toolchain:
        toolchain.extractall(project_path / 'toolchain.temp', filter='data')
    (project_path / 'toolchain.tar.xz').unlink()
    (project_path / 'toolchain.temp' / TOOLCHAIN_NAME).rename(project_path / 'toolchain')
    (project_path / 'toolchain.temp').rmdir()

def prepare_rkbin():
    rmtree(project_path / 'rkbin', ignore_errors=True)
    Repo(project_path / 'rkbin.git').clone(project_path / 'rkbin', depth=1, branch='master')

def build_common(type, config, binaries):
    uboot_repo_name = f'u-boot-{type}'
    branch = REPOS[uboot_repo_name]['branch']
    output_archive_name = f"rkloader_{type}_{branch}_{config}"
    output_archive_name += '_bl31_' + binaries['BL31'].stem.split('_')[-1]
    output_archive_name += '_ddr_' + binaries['DDR'].stem.split('_')[-1]
    output_archive_path = project_path / RKLOADERS_PATH / f'{output_archive_name}.img'
    report_name=f'u-boot ({type}) for {config}'
    with open(project_path / RKLOADERS_PATH / 'list', 'a') as f:
        f.write(f"{type}:{config}:{output_archive_name}.img.gz\n")
    if (project_path / RKLOADERS_PATH / f'{output_archive_name}.img.gz').is_file():
        print(f'Skipped building {report_name}')
        return
    Repo(project_path / f'{uboot_repo_name}.git').clone(project_path / 'build', depth=1, branch=branch)
    print(f'Configuring {report_name}')
    assert sp.run(['make', '-C', 'build', f'{config}_defconfig']).returncode == 0
    print(f'Building {report_name}')
    output_archive_path.unlink(missing_ok=True)
    match type:
        case 'vendor':
            assert sp.run(['make', '-C', 'build', '-j', str(os.cpu_count()), 'spl/u-boot-spl.bin', 'u-boot.dtb', 'u-boot.itb'], 
                          env={'PATH':f'{os.environ['PATH']}:{Path('toolchain/bin').resolve()}',
                               'BL31':binaries['BL31'].as_posix(),
                               'ARCH':'arm64', 
                               'CROSS_COMPILE':'aarch64-linux-gnu-'}).returncode == 0
            assert sp.run(['build/tools/mkimage', '-n', 'rk3588', '-T', 'rksd', 
                           '-d', binaries['DDR'].as_posix() + ":build/spl/u-boot-spl.bin",
                           'build/idbloader.img'])
            with open(output_archive_path, 'wb') as file:
                file.truncate(4 * 1024 * 1024)
            proc = sp.Popen(['sfdisk', output_archive_path], stdin = sp.PIPE)
            proc.communicate(REPOS[uboot_repo_name]['gpt'])
            assert proc.wait() == 0
            assert sp.run(['dd', 'if=build/idbloader.img', f'of={output_archive_path}', 'seek=64', 'conv=notrunc']).returncode == 0
            assert sp.run(['dd', 'if=build/u-boot.itb', f'of={output_archive_path}', 'seek=1024', 'conv=notrunc']).returncode == 0
        case 'mainline':
            assert sp.run(['/usr/bin/make', '-C', 'build', '-j', str(os.cpu_count())], 
                          env={'PATH':f'{os.environ['PATH']}:{Path('toolchain/bin').resolve()}',
                               'BL31':binaries['BL31'].as_posix(), 
                               'ROCKCHIP_TPL':binaries['DDR'].as_posix(),
                               'ARCH':'arm64', 
                               'CROSS_COMPILE':'aarch64-linux-gnu-'}).returncode == 0
            with open(output_archive_path, 'wb') as file:
                file.truncate(17 * 1024 * 1024)
            proc = sp.Popen(['sfdisk', output_archive_path], stdin = sp.PIPE)
            proc.communicate(REPOS[uboot_repo_name]['gpt'])
            assert proc.wait() == 0
            assert sp.run(['dd', 'if=build/u-boot-rockchip.bin', f'of={output_archive_path}', 'seek=64', 'conv=notrunc']).returncode == 0
            
    assert sp.run(['gzip', '-9', '--force', '--suffix', '.gz', output_archive_path]).returncode == 0
    rmtree(project_path / 'build')

def build_all():
    rmtree(project_path / 'build', ignore_errors=True)
    (project_path / RKLOADERS_PATH / 'list').unlink(missing_ok=True)
    (project_path / RKLOADERS_PATH).mkdir(exist_ok=True)
    # get absolute paths to the latest versions of BL31 and DDR
    binaries = {
        'BL31': sorted(Path('rkbin/rk35').glob('rk3588_bl31_*'), reverse=True)[0].resolve(),
        'DDR': sorted(Path('rkbin/rk35').glob('rk3588_ddr_lp4_2112MHz_lp5_2736MHz_*'), reverse=True)[0].resolve()
    }
    for config in REPOS['u-boot-mainline']['board-configs']:
        build_common('mainline', config, binaries)
    for config in REPOS['u-boot-vendor']['board-configs']:
        build_common('vendor', config, binaries)
    rmtree(project_path / 'rkbin')
    
def checksums():
    sums = ''
    with open(project_path / RKLOADERS_PATH / 'list', 'rb') as list_file:
        sums += f"{hashlib.file_digest(list_file, 'sha512').hexdigest()} list\n"
    with open(project_path / RKLOADERS_PATH / 'list', 'r') as list_file:
        for line in list_file.read().splitlines():
            file_name = line.split(':')[2]
            with open(project_path / RKLOADERS_PATH / file_name, 'rb') as file:
                sums += f"{hashlib.file_digest(file, 'sha512').hexdigest()} {file_name}\n"
    with open(project_path / RKLOADERS_PATH / 'sha512sums', 'w') as file:
        file.write(sums)

def main():
    global project_path
    project_path = Path(__file__).parent
    update_all_repos()
    deploy_toolchain()
    prepare_rkbin()
    build_all()
    checksums()

if __name__ == '__main__':
    main()
