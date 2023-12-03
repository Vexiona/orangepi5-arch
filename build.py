import subprocess as sp
import os
import re

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

TOOLCHAIN_VENDOR='gcc-linaro-7.4.1-2019.02-x86_64_aarch64-linux-gnu'

MIRROR_ARMBIAN='https://redirect.armbian.com'

def init_repo(dir, url, branch = '*'):
    sp.run(['rm', '-rf', dir])
    # sp.run(['mkdir', dir])
    sp.run(['git', 'clone', '--bare', url, dir])
#     sp.run(['mkdir', os.path.join(dir, 'objects')])
#     sp.run(['mkdir', os.path.join(dir, 'refs')])
#     with open(os.path.join(dir, 'HEAD'), 'w') as f:
#         f.write('ref: refs/heads/' + branch)
#     with open(os.path.join(dir, 'config'), 'w') as f:
#         f.write(f"""[core]
# \trepositoryformatversion = 0
# \tfilemode = true
# \tbare = true
# [remote "origin"]
# \turl = {url}
# \tfetch = +refs/heads/{branch}:refs/heads/{branch}
# """)

def update_repo(dir, url, branch):
    if not os.path.isdir(dir):
        init_repo(dir, url, branch)
    print(f"Updating local git '{dir}' from '{url}'")
    sp.run(['git', '--git-dir', dir, 'remote', 'update', '--prune'])

def update_all_repos():
    for repo, data in REPOS.items():
        update_repo(repo + '.git', data['url'], data['branch'])

def deploy_toolchain_vendor():
    if os.path.isdir('toolchain-vendor'):
        print('Toolchain exists. Reusing')
        return
    print(f'Deploying toolchain {TOOLCHAIN_VENDOR}')
    sp.run(['rm', '-rf', 'toolchain-vendor.temp'])
    sp.run(['mkdir', 'toolchain-vendor.temp'])
    sp.run(['wget', f'{MIRROR_ARMBIAN}/_toolchain/{TOOLCHAIN_VENDOR}.tar.xz', '-O', 'toolchain.tar.xz'])
    print('Extracting toolchain')
    sp.run(['tar', '-C', 'toolchain-vendor.temp', '--strip-components', '1', '-xJf', 'toolchain.tar.xz'])
    print('Done: Extracting toolchain')
    sp.run(['rm', '-vf', 'toolchain.tar.xz'])
    sp.run(['mv', 'toolchain-vendor.temp', 'toolchain-vendor'])
    print(f'Done: Deploying toolchain {TOOLCHAIN_VENDOR}')

def prepare_rkbin():
    sp.run(['rm', '-rf', 'rkbin'])
    sp.run(['mkdir', 'rkbin'])
    sp.run(['git', '--git-dir', 'rkbin.git', '--work-tree', 'rkbin', 'checkout', '-f', 'master'])

def find_latest_binaries():
    bl31 = None
    ddr = None
    re_bl31 = re.compile('rk3588_bl31_.*')
    re_ddr = re.compile('rk3588_ddr_lp4_2112MHz_lp5_2736MHz_.*')
    for binary in sorted(os.listdir('rkbin/rk35'), reverse = True):
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
    branch = REPOS['u-boot-' + type]['branch']
    output_archive_name = f"rkloader_{type}_{branch}_{config}"
    output_archive_name += '_bl31_' + binaries['BL31'].split('_')[-1].removesuffix('.elf')
    output_archive_name += '_ddr_' + binaries['DDR'].split('_')[-1].removesuffix('.bin')
    with open('out/list', 'a') as f:
        f.write(f"{type}:{config}:{output_archive_name}.img.gz\n")
    output_archive_path = 'out/' + output_archive_name + '.img'
    report_name=f'u-boot ({type}) for {config}'
    if os.path.isfile(output_archive_path + '.gz'):
        print(f'Skipped building {report_name}')
        return None 
    sp.run(['mkdir', 'build'])
    sp.run(['git', '--git-dir', f'u-boot-{type}.git', '--work-tree', 'build', 'checkout', '-f', branch])
    print(f'Configuring {report_name}')
    sp.run(['make', '-C', 'build', f'{config}_defconfig'])
    print(f'Building {report_name}')
    sp.run(['rm', '-vf', output_archive_path])
    match type:
        case 'vendor':
            sp.run(['make', '-C', 'build', '-j', str(os.cpu_count()), 'spl/u-boot-spl.bin', 'u-boot.dtb', 'u-boot.itb'], 
                   env={**os.environ, 'BL31':f"rkbin/rk35/{binaries['BL31']}",
                        'ARCH':'arm64', 'CROSS_COMPILE':'aarch64-linux-gnu-'})
            sp.run(['build/tools/mkimage', '-n', 'rk3588', '-T', 'rksd', 
                    '-d', f"rkbin/rk35/{binaries['DDR']}",
                    'build/idbloader.img'])
            sp.run(['truncate', '-s', '4M', output_archive_path])
            proc = sp.Popen(['sfdisk', output_archive_path], stdin = sp.PIPE)
            proc.communicate(REPOS['u-boot-' + type]['gpt'])
            sp.run(['dd', 'if=build/idbloader.img', f'of={output_archive_path}', 'seek=64', 'conv=notrunc'])
            sp.run(['dd', 'if=build/u-boot.itb', f'of={output_archive_path}', 'seek=1024', 'conv=notrunc'])
        case 'mainline':
            sp.run(['make', '-C', 'build', '-j', str(os.cpu_count())], 
                   env={**os.environ, 
                        'BL31':os.path.abspath(f"rkbin/rk35/{binaries['BL31']}"), 
                        'ROCKCHIP_TPL':os.path.abspath(f"rkbin/rk35/{binaries['DDR']}"),
                        'ARCH':'arm64', 'CROSS_COMPILE':'aarch64-linux-gnu-'})
            sp.run(['truncate', '-s', '17M', output_archive_path])
            proc = sp.Popen(['sfdisk', output_archive_path], stdin = sp.PIPE)
            proc.communicate(REPOS['u-boot-' + type]['gpt'])
            sp.run(['dd', 'if=build/u-boot-rockchip.bin', f'of={output_archive_path}', 'seek=64', 'conv=notrunc'])
    sp.run(['gzip', '-9', '--force', '--suffix', '.gz', output_archive_path])
    sp.run(['rm', '-rf', 'build'])

def build_all():
    sp.run(['rm', '-rf', 'build', 'out/list'])
    sp.run(['mkdir', '-p', 'out'])
    binaries = find_latest_binaries()
    for config in REPOS['u-boot-mainline']['board-configs']:
        build_common('mainline', config, binaries)
    for config in REPOS['u-boot-vendor']['board-configs']:
        build_common('vendor', config, binaries)
    sp.run(['rm', '-rf', 'rkbin'])

def main():
    update_all_repos()
    deploy_toolchain_vendor()
    prepare_rkbin()
    build_all()

if __name__ == '__main__':
    main()
