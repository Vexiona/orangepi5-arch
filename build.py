import subprocess as sp
import os

uboot_vendor_repo_url='https://github.com/orangepi-xunlong/u-boot-orangepi.git'
uboot_vendor_branch='v2017.09-rk3588'

uboot_mainline_repo_url='https://github.com/u-boot/u-boot'
uboot_mainline_branch='master'

rkbin_repo_url='https://github.com/armbian/rkbin.git'
rkbin_branch='master'

configs_vendor=('orangepi_5b', 'orangepi_5', 'orangepi_5_plus', 'orangepi_5_sata')
configs_mainline=('orangepi-5-plus-rk3588', 'orangepi-5-rk3588s')

toolchain_vendor='gcc-linaro-7.4.1-2019.02-x86_64_aarch64-linux-gnu'

armbian_mirror='https://redirect.armbian.com'

gpt_vendor="""label: gpt
first-lba: 34
start=64, size=960, type=8DA63339-0007-60C0-C436-083AC8230908, name="idbloader"
start=1024, size=6144, type=8DA63339-0007-60C0-C436-083AC8230908, name="uboot"
"""

# The image is 9.1 MiB, for safety we have 16 MiB, 
gpt_mainline="""label: gpt
first-lba: 64
start=64, size=32704, type=8DA63339-0007-60C0-C436-083AC8230908, name="uboot"
"""

# Init a repo
def init_repo(dir, url, branch = '*'):
    sp.run(['rm', '-rf', dir])
    sp.run(['mkdir', dir])
    sp.run(['mkdir', os.path.join(dir, 'objects')])
    sp.run(['mkdir', os.path.join(dir, 'refs')])
    with open(os.path.join(dir, 'HEAD'), 'w') as f:
        f.write('ref: refs/heads/' + branch)
    with open(os.path.join(dir, 'config'), 'w') as f:
        f.write(f"""[core]
\trepositoryformatversion = 0
\tfilemode = true
\tbare = true
[remote "origin"]
\turl = {url}
\tfetch = +refs/heads/{branch}:refs/heads/{branch}
""")

def update_repo(dir, url, branch):
    if not os.path.isdir(dir):
        init_repo(dir, url, branch)
    if not os.path.isdir(dir):
        raise FileNotFoundError(f"Failed to prepare local git '{dir}' from '{url}'")
    print(f"Updating local git '{dir}' from '{url}'")
    sp.run(['git', '--git-dir', dir, 'remote', 'update', '--prune'])

def update_all_repos():
    update_repo('u-boot-vendor.git', uboot_vendor_repo_url, uboot_vendor_branch)
    update_repo('u-boot-mainline.git', uboot_mainline_repo_url, uboot_mainline_branch)
    update_repo('rkbin.git', rkbin_repo_url, rkbin_branch)

def deploy_toolchain_vendor():
    if os.path.isdir('toolchain-vendor'):
        print('Toolchain exists. Reusing')
        return
    print(f'Deploying toolchain {toolchain_vendor}')
    sp.run(['rm', '-rf', 'toolchain-vendor.temp'])
    sp.run(['mkdir', 'toolchain-vendor.temp'])
    sp.run(['wget', f'{armbian_mirror}/_toolchain/{toolchain_vendor}.tar.xz', '-O', 'toolchain.tar.xz'])
    print('Extracting toolchain')
    sp.run(['tar', '-C', 'toolchain-vendor.temp', '--strip-components', '1', '-xJf', 'toolchain.tar.xz'])
    print('Done: Extracting toolchain')
    sp.run(['rm', '-vf', 'toolchain.tar.xz'])
    sp.run(['mv', 'toolchain-vendor.temp', 'toolchain-vendor'])
    print(f'Done: Deploying toolchain {toolchain_vendor}')

def prepare_rkbin():
    sp.run(['rm', '-rf', 'rkbin'])
    sp.run(['mkdir', 'rkbin'])
    sp.run(['git', '--git-dir', 'rkbin.git', '--work-tree', 'rkbin', 'checkout', '-f', 'master'])
    # path_bl31 = sp.run(['ls'])

def build_common(type, branch, config):
    name='rkloader_' + type + branch + config
    match type:
        case 'vendor':
            name += 'vendor_version'
        case 'mainline':
            name += 'mainline_version'
        case _:
            raise ValueError(f"Expected 'vendor' or 'mainline', got '{type}'")
    with open('out/list', 'a') as f:
        f.write(f"{type}:{config}:{name}.img.gz")
    output_archive_path = 'out/' + name + '.img'
    # outs+=("{out}")
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
                   env={**os.environ, 'BL31':'rkbin/rk35/rk3588_bl31_v1.42.elf',
                        'ARCH':'arm64', 'CROSS_COMPILE':'aarch64-linux-gnu-'})
            sp.run(['build/tools/mkimage', '-n', 'rk3588', '-T', 'rksd', 
                    '-d', 'rkbin/rk35/rk3588_ddr_lp4_2112MHz_lp5_2736MHz_v1.13.bin',
                    'build/idbloader.img'])
            sp.run(['truncate', '-s', '4M', output_archive_path])
            proc = sp.Popen(['sfdisk', output_archive_path], stdin = sp.PIPE)
            proc.communicate(b"""label: gpt
first-lba: 34
start=64, size=960, type=8DA63339-0007-60C0-C436-083AC8230908, name="idbloader"
start=1024, size=6144, type=8DA63339-0007-60C0-C436-083AC8230908, name="uboot"
""")
            sp.run(['dd', 'if=build/idbloader.img', f'of={output_archive_path}', 'seek=64', 'conv=notrunc'])
            sp.run(['dd', 'if=build/u-boot.itb', f'of={output_archive_path}', 'seek=1024', 'conv=notrunc'])
        case 'mainline':
            sp.run(['make', '-C', 'build', '-j', str(os.cpu_count())], 
                   env={**os.environ, 
                        'BL31':os.path.abspath('rkbin/rk35/rk3588_bl31_v1.42.elf'), 
                        'ROCKCHIP_TPL':os.path.abspath('rkbin/rk35/rk3588_ddr_lp4_2112MHz_lp5_2736MHz_v1.13.bin'),
                        'ARCH':'arm64', 'CROSS_COMPILE':'aarch64-linux-gnu-'})
            sp.run(['truncate', '-s', '17M', output_archive_path])
            proc = sp.Popen(['sfdisk', output_archive_path], stdin = sp.PIPE)
            proc.communicate(b"""label: gpt
first-lba: 64
start=64, size=32704, type=8DA63339-0007-60C0-C436-083AC8230908, name="uboot"
""")
            sp.run(['dd', 'if=build/u-boot-rockchip.bin', f'of={output_archive_path}', 'seek=64', 'conv=notrunc'])
    sp.run(['gzip', '-9', '--force', '--suffix', '.gz', output_archive_path])
    sp.run(['rm', '-rf', 'build'])

def build_all():
    sp.run(['rm', '-rf', 'build', 'out/list'])
    sp.run(['mkdir', '-p', 'out'])
    for config in configs_mainline:
        build_common('mainline', uboot_mainline_branch, config)
    for config in configs_vendor:
        build_common('vendor', uboot_vendor_branch, config)
    sp.run(['rm', '-rf', 'rkbin'])

update_all_repos()
deploy_toolchain_vendor()
prepare_rkbin()
build_all()
