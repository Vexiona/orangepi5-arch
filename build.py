import subprocess as sp
import os
import re
from git import Repo
from pathlib import Path
from shutil import rmtree
import hashlib

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

OUT_PATH = 'rkloaders'

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

def deploy_toolchain_vendor():
    if os.path.isdir('toolchain-vendor'):
        print('Toolchain exists. Reusing')
        return
    print(f'Deploying toolchain {TOOLCHAIN_NAME}')
    rmtree(project_path / 'toolchain-vendor.temp', ignore_errors=True)
    (project_path / 'toolchain-vendor.temp').mkdir()
    assert sp.run(['wget', f'{TOOLCHAIN_URL}{TOOLCHAIN_NAME}.tar.xz', '-O', 'toolchain.tar.xz']).returncode == 0
    assert sp.run(['tar', '-C', 'toolchain-vendor.temp', '--strip-components', '1', '-xJf', 'toolchain.tar.xz']).returncode == 0
    (project_path / 'toolchain.tar.xz').unlink(missing_ok=True)
    (project_path / 'toolchain-vendor.temp').rename('toolchain-vendor')

def prepare_rkbin():
    rmtree(project_path / 'rkbin', ignore_errors=True)
    Repo(project_path / 'rkbin.git').clone(project_path / 'rkbin', depth=1, branch='master')

def find_latest_binaries():
    bl31 = None
    ddr = None
    re_bl31 = re.compile('rk3588_bl31_.*')
    re_ddr = re.compile('rk3588_ddr_lp4_2112MHz_lp5_2736MHz_.*')
    for binary in sorted(os.listdir('rkbin/rk35'), reverse=True):
        if bl31 == None:
            bl31 = re.match(re_bl31, binary)
        if ddr == None:
            ddr = re.match(re_ddr, binary)
    bl31 = bl31.group(0)
    ddr = ddr.group(0)
    return {
        'BL31': bl31,
        'DDR': ddr
    }

def build_common(type, config, binaries):
    uboot_repo_name = f'u-boot-{type}'
    branch = REPOS[uboot_repo_name]['branch']
    output_archive_name = f"rkloader_{type}_{branch}_{config}"
    output_archive_name += '_bl31_' + binaries['BL31'].split('_')[-1].removesuffix('.elf')
    output_archive_name += '_ddr_' + binaries['DDR'].split('_')[-1].removesuffix('.bin')
    output_archive_path = project_path / OUT_PATH / f'{output_archive_name}.img'
    report_name=f'u-boot ({type}) for {config}'
    with open(project_path / OUT_PATH / 'list', 'a') as f:
        f.write(f"{type}:{config}:{output_archive_name}.img.gz\n")
    if os.path.isfile(project_path / OUT_PATH / f'{output_archive_name}.img.gz'):
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
                          env={**os.environ, 'BL31':Path(f"rkbin/rk35/{binaries['BL31']}").resolve().as_posix(),
                               'ARCH':'arm64', 'CROSS_COMPILE':'aarch64-linux-gnu-'}).returncode == 0
            assert sp.run(['build/tools/mkimage', '-n', 'rk3588', '-T', 'rksd', 
                           '-d', Path(f"rkbin/rk35/{binaries['DDR']}").resolve().as_posix() + ":build/spl/u-boot-spl.bin",
                           'build/idbloader.img'])
            assert sp.run(['truncate', '-s', '4M', output_archive_path]).returncode == 0
            proc = sp.Popen(['sfdisk', output_archive_path], stdin = sp.PIPE)
            proc.communicate(REPOS[uboot_repo_name]['gpt'])
            assert proc.wait() == 0
            assert sp.run(['dd', 'if=build/idbloader.img', f'of={output_archive_path}', 'seek=64', 'conv=notrunc']).returncode == 0
            assert sp.run(['dd', 'if=build/u-boot.itb', f'of={output_archive_path}', 'seek=1024', 'conv=notrunc']).returncode == 0
        case 'mainline':
            assert sp.run(['make', '-C', 'build', '-j', str(os.cpu_count())], 
                          env={**os.environ, 
                               'BL31':os.path.abspath(f"rkbin/rk35/{binaries['BL31']}"), 
                               'ROCKCHIP_TPL':os.path.abspath(f"rkbin/rk35/{binaries['DDR']}"),
                               'ARCH':'arm64', 'CROSS_COMPILE':'aarch64-linux-gnu-'}).returncode == 0
            assert sp.run(['truncate', '-s', '17M', output_archive_path]).returncode == 0
            proc = sp.Popen(['sfdisk', output_archive_path], stdin = sp.PIPE)
            proc.communicate(REPOS[uboot_repo_name]['gpt'])
            assert proc.wait() == 0
            assert sp.run(['dd', 'if=build/u-boot-rockchip.bin', f'of={output_archive_path}', 'seek=64', 'conv=notrunc']).returncode == 0
    assert sp.run(['gzip', '-9', '--force', '--suffix', '.gz', output_archive_path]).returncode == 0
    rmtree(project_path / 'build')

def build_all():
    rmtree(project_path / 'build', ignore_errors=True)
    (project_path / OUT_PATH / 'list').unlink(missing_ok=True)
    (project_path / OUT_PATH).mkdir(exist_ok=True)
    binaries = find_latest_binaries()
    for config in REPOS['u-boot-mainline']['board-configs']:
        build_common('mainline', config, binaries)
    for config in REPOS['u-boot-vendor']['board-configs']:
        build_common('vendor', config, binaries)
    rmtree(project_path / 'rkbin')
    
def checksums():
    sums = ''
    list_file = open(f'{OUT_PATH}/list', 'r')
    for line in list_file.read().splitlines():
        file_name = line.split(':')[2]
        with open(project_path / OUT_PATH / file_name, 'rb') as file:
            sums += f"{hashlib.file_digest(file, 'sha512').hexdigest()} {file_name}\n"
    with open(project_path / OUT_PATH / 'sha512sums', 'w') as file:
        file.write(sums)

def main():
    global project_path
    project_path = Path(__file__).parent
    update_all_repos()
    deploy_toolchain_vendor()
    prepare_rkbin()
    build_all()
    checksums()

if __name__ == '__main__':
    main()
