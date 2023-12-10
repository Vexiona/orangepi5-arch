import subprocess as sp
import os
from git import Repo
from pathlib import Path
from shutil import rmtree
import hashlib
import tarfile

reap_children() { #1 kill arg
    local children child
    local i=0
    while [[ $i -lt 100 ]]; do
        children=($(pgrep -P$$))
        if [[ "${#children[@]}" -eq 1 ]]; then # Only pgrep
            return
        fi
        for child in "${children[@]}"; do
            kill "$@" "${child}" 2>/dev/null || true
        done
        i=$(( $i + 1 ))
        sleep 1
    done
}

cleanup_parent() {
    echo "=> Cleaning up before exiting (parent)..."
    # The root mount only lives inside the child namespace, no need to umount
    rm -rf cache/root
    reap_children
}

cleanup_child() {
    echo "=> Cleaning up before exiting (child)..."
    mount proc /proc -t proc -o nosuid,noexec,nodev
    reap_children -9
}

get_uid_gid() {
    uid=$(id --user)
    gid=$(id --group)
}

check_identity_root() {
    get_uid_gid
    if [[ "${uid}" != 0 ]]; then
        echo "ERROR: Must run as root (UID = 0)"
        exit 1
    fi
    if [[ "${gid}" != 0 ]]; then
        echo "ERROR: Must run as GID = 0"
        exit 1
    fi
}

check_identity_non_root() {
    get_uid_gid
    if [[ "${uid}" == 0 ]]; then
        echo "ERROR: Not allowed to run as root (UID = 0)"
        exit 1
    fi
    if [[ "${gid}" == 0 ]]; then
        echo "ERROR: Not allowed to run as GID = 0"
        exit 1
    fi
}

config_repos() {
    mirror_archlinux=${mirror_archlinux:-https://geo.mirror.pkgbuild.com}
    mirror_archlinuxarm=${mirror_alarm:-http://mirror.archlinuxarm.org}
    mirror_7Ji=${mirror_7Ji:-https://github.com/7Ji/archrepo/releases/download}
    # For base system packages
    repo_url_alarm_aarch64="${mirror_archlinuxarm}"/aarch64/'$repo'
    # For kernels and other stuffs
    repo_url_7Ji_aarch64="${mirror_7Ji}"/aarch64
}

prepare_host_dirs() {
    rm -rf cache
    mkdir -p {bin,cache/root,out,pkg}
}

get_rkloaders() {
    local sum=$(sed -n 's/\(^[0-9a-f]\{128\}\) \+list$/\1/p' rkloader/sha512sums)
    if [[ $(sha512sum rkloader/list | cut -d ' ' -f 1) !=  "${sum}" ]]; then
        echo 'ERROR: list sha512sum not right'
        false
    fi
    # local rkloader model name
    rkloaders=($(<rkloader/list))
    for rkloader in "${rkloaders[@]}"; do
        name="${rkloader##*:}"
        sum=$(sed -n 's/\(^[0-9a-f]\{128\}\) \+'${name}'$/\1/p' rkloader/sha512sums)
        if [[ ! ($(sha512sum rkloader/"${name}" | cut -d ' ' -f 1) ==  "${sum}") ]]; then
            false
        fi
    done
}

prepare_pacman_static() {
    return 0
}

prepare_pacman_configs() {
    # Create temporary pacman config
    pacman_config="
RootDir      = cache/root
DBPath       = cache/root/var/lib/pacman/
CacheDir     = pkg/
LogFile      = cache/root/var/log/pacman.log
GPGDir       = cache/root/etc/pacman.d/gnupg/
HookDir      = cache/root/etc/pacman.d/hooks/
Architecture = aarch64"
    pacman_mirrors="
[core]
Server = ${repo_url_alarm_aarch64}
[extra]
Server = ${repo_url_alarm_aarch64}
[alarm]
Server = ${repo_url_alarm_aarch64}
[aur]
Server = ${repo_url_alarm_aarch64}
[7Ji]
Server = ${repo_url_7Ji_aarch64}"

    echo "[options]${pacman_config}
SigLevel = Never${pacman_mirrors}" > cache/pacman-loose.conf

    echo "[options]${pacman_config}
SigLevel = DatabaseOptional${pacman_mirrors}" > cache/pacman-strict.conf
}

enable_network() {
    cat /etc/resolv.conf > cache/resolv.conf
    mount cache/resolv.conf cache/root/etc/resolv.conf -o bind
}

disable_network() {
    umount cache/root/etc/resolv.conf
}

image_disk() {
    local image=out/"${build_id}"-base.img
    local temp_image="${image}".temp
    rm -f "${temp_image}"
    truncate -s "${spart_size_all}"M "${temp_image}"
    echo "label: ${spart_label}
${spart_boot}
${spart_root}" | sfdisk "${temp_image}"
    dd if=cache/boot.img of="${temp_image}" bs=1M seek="${spart_off_boot}" conv=notrunc
    dd if=cache/root.img of="${temp_image}" bs=1M seek="${spart_off_root}" conv=notrunc
    sync
    mv "${temp_image}" "${image}"
}

image_rkloader() {
    suffixes=(root.tar base.img)
    local table="label: ${spart_label}
first-lba: ${spart_firstlba}
${spart_idbloader}
${spart_uboot}
${spart_boot}
${spart_root}"
    local base_image=out/"${build_id}"-base.img
    local rkloader model image temp_image suffix fdt kernel pattern_remove_overlay= pattern_set_overlay=
    for kernel in "${install_pkgs_kernel[@]}"; do
        pattern_remove_overlay+=';/^\tFDTOVERLAYS\t'"${kernel}"'$/d'
        pattern_set_overlay+=';s|^\tFDTOVERLAYS\t'"${kernel}"'$|\tFDTOVERLAYS\t/dtbs/'"${kernel}"'/rockchip/overlay/rk3588-ssd-sata0.dtbo|'
    done
    for rkloader in "${rkloaders[@]}"; do
        type="${rkloader%%:*}"
        if [[ ${type} != vendor ]]; then
            continue
        fi
        model="${rkloader%:*}"
        model="${model#*:}"
        name="${rkloader##*:}"
        suffix="rkloader-${model}".img
        suffixes+=("${suffix}")
        image=out/"${build_id}"-"${suffix}"
        temp_image="${image}".temp
        # Use cp as it could reflink if the fs supports it
        cp "${base_image}" "${temp_image}"
        gzip -cdk rkloader/"${name}" | dd of="${temp_image}" conv=notrunc
        sfdisk "${temp_image}" <<< "${table}"
        case ${model#orangepi_} in
        5b)
            fdt='rk3588s-orangepi-5b.dtb'
        ;;
        5_plus)
            fdt='rk3588-orangepi-5-plus.dtb'
        ;;
        *) # 5, 5_sata
            fdt='rk3588s-orangepi-5.dtb'
        ;;
        esac
        # \n\tFDTOVERLAYS\t/dtbs/linux-aarch64-orangepi5/rockchip/overlay/rk3588-ssd-sata0.dtbo
        sed 's|rk3588s-orangepi-5.dtb|'"${fdt}"'|' cache/extlinux.conf > cache/extlinux.conf.temp
        if [[ ${model} == '5_sata' ]]; then
            sed -i "${pattern_set_overlay}" cache/extlinux.conf.temp
        else
            sed -i "${pattern_remove_overlay}" cache/extlinux.conf.temp
        fi
        mcopy -oi cache/boot.img cache/extlinux.conf.temp ::extlinux/extlinux.conf
        sync
        dd if=cache/boot.img of="${temp_image}" bs=1M seek=4 conv=notrunc
        mv "${temp_image}" "${image}"
    done
}

release() {
    pids_gzip=()
    rm -rf out/latest
    mkdir out/latest
    for suffix in "${suffixes[@]}"; do
        # gzip -9 out/"${build_id}-${suffix}" &
        # pids_gzip+=($!)
        ln -s ../"${build_id}-${suffix}".gz out/latest/
    done
    echo "Waiting for gzip processes to end..."
    wait ${pids_gzip[@]}
}

get_subid() { #1 name #2 uid, #3 type
    local subid=$(grep '^'"$1"':[0-9]\+:[0-9]\+$' /etc/"$3" | tail -1)
    if [[ -z "${subid}" ]]; then
        subid=$(grep '^'"$2"':[0-9]\+:[0-9]\+$' /etc/"$3" | tail -1)
    fi
    if [[ -z "${subid}" ]]; then
        echo "ERROR: failed to get $3 for current user"
        exit 1
    fi
    echo "${subid}"
}

spawn_and_wait() {
    local username=$(id --user --name)
    if [[ -z "${username}" ]]; then
        echo 'ERROR: Failed to get user name of current user'
        exit 1
    fi
    local subuid=$(get_subid "${username}" "${uid}" subuid)
    local subgid=$(get_subid "${username}" "${uid}" subgid)
    local uid_range="${subuid##*:}"
    # We need to map the user to 0:0, and others to 1:65535
    if [[ "${uid_range}" -lt 65535 ]]; then
        echo 'ERROR: subuid range too short'
        exit 1
    fi
    local gid_range="${subgid##*:}"
    if [[ "${gid_range}" -lt 65535 ]]; then
        echo 'ERROR: subgid range too short'
        exit 1
    fi
    local uid_start="${subuid#*:}"
    uid_start="${uid_start%:*}"
    local gid_start="${subgid#*:}"
    gid_start="${gid_start%:*}"
    
    local args=()
    local arg=
    for arg in "${install_pkgs_bootstrap[@]}"; do
        args+=(--install-bootstrap "$arg")
    done
    for arg in "${install_pkgs_normal[@]}"; do
        args+=(--install "$arg")
    done
    for arg in "${install_pkgs_kernel[@]}"; do
        args+=(--install-kernel "$arg")
    done
    # Note: the options --map-users and --map-groups were added to unshare.1 in 
    # util-linux 2.38, which was released on Mar 28, 2022. But the main distro
    # I aim, Ubuntu 22.04, which is what Github Actions use, packs an older
    # util-linux 2.37, which does not have those arguments. So we have to work
    # around this by calling newuidmap and newgidmap directly.
    unshare --user --pid --mount --fork \
        /bin/bash -e "${arg0}" --role child --uuid-root "${uuid_root}" --uuid-boot "${uuid_boot}" --build-id "${build_id}"  "${args[@]}" &
    pid_child="$!"
    newuidmap "${pid_child}" 0 "${uid}" 1 1 "${uid_start}" 65535
    newgidmap "${pid_child}" 0 "${gid}" 1 1 "${gid_start}" 65535
    wait "${pid_child}"
    pid_child=
}

set_parts() {
    spart_label='gpt'
    # lba size=17K
    spart_firstlba='34'
    # start=32K
    spart_idbloader='start=64, size=960, type=8DA63339-0007-60C0-C436-083AC8230908, name="idbloader"'
    # start=512K
    spart_uboot='start=1024, size=6144, type=8DA63339-0007-60C0-C436-083AC8230908, name="uboot"'
    spart_size_all=2048
    spart_off_boot=4
    spart_size_boot=256
    local skt_off_boot=$(( ${spart_off_boot} * 2048 ))
    local skt_size_boot=$(( ${spart_size_boot} * 2048 ))
    # start=4M size=256M
    spart_boot='start='"${skt_off_boot}"', size='"${skt_size_boot}"', type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name="alarmboot"'
    spart_off_root=$(( ${spart_off_boot} + ${spart_size_boot} ))
    spart_size_root=$((  ${spart_size_all} - 1 - ${spart_off_root} ))
    local skt_off_root=$(( ${spart_off_root} * 2048 ))
    local skt_size_root=$(( ${spart_size_root} * 2048 ))
    # start=(4+256)M=260M size=2048-1-260=1787M end=2048
    spart_root='start='"${skt_off_root}"', size='"${skt_size_root}"', type=B921B045-1DF0-41C3-AF44-4C6F280D3FAE, name="alarmroot"'
}

prepare_host() {
    prepare_host_dirs
    get_rkloaders
    config_repos
    prepare_pacman_static
    prepare_pacman_configs
}

cleanup_cache() {
    rm -rf cache
}

work_parent() {
    trap "cleanup_parent" INT TERM EXIT
    check_identity_non_root
    prepare_host
    spawn_and_wait
    # The child should have prepared the following artifacts: cache/root.img cache/boot.img cache/extlinux.conf
    # And the child should have already finished out/*-root.tar
    set_parts
    image_disk
    image_rkloader
    release
    cleanup_cache
}

build_id=ArchLinuxARM-aarch64-OrangePi5-$(date +%Y%m%d_%H%M%S)
install_pkgs_bootstrap=(base archlinuxarm-keyring 7ji-keyring)
install_pkgs_normal=(vim nano sudo openssh linux-firmware-orangepi-git usb2host)
install_pkgs_kernel=(linux-aarch64-orangepi5{,-git})
uuid_root=$(uuidgen)
uuid_boot=$(uuidgen)

work_parent