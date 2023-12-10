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

cleanup_child() {
    echo "=> Cleaning up before exiting (child)..."
    mount proc /proc -t proc -o nosuid,noexec,nodev
    reap_children -9
}

check_identity_root() {
    uid=$(id --user)
    gid=$(id --group)
    if [[ "${uid}" != 0 ]]; then
        echo "ERROR: Must run as root (UID = 0)"
        exit 1
    fi
    if [[ "${gid}" != 0 ]]; then
        echo "ERROR: Must run as GID = 0"
        exit 1
    fi
}

check_identity_map_root() {
    check_identity_root
    if touch /sys/sys_write_test; then
        echo "Child: ERROR: We can write to /sys, refuse to continue as real root"
        exit 1
    fi
}

mount_root() {
    mount tmpfs-root cache/root -t tmpfs -o mode=0755,nosuid 
    mkdir -p cache/root/{boot,dev/{pts,shm},etc/pacman.d,proc,run,sys,tmp,var/{cache/pacman/pkg,lib/pacman,log}}
    chmod 1777 cache/root/{dev/shm,tmp}
    chmod 555 cache/root/{proc,sys}
    mount proc cache/root/proc -t proc -o nosuid,noexec,nodev
    mount devpts cache/root/dev/pts -t devpts -o mode=0620,gid=5,nosuid,noexec
    for node in full null random tty urandom zero; do
        devnode=cache/root/dev/"${node}"
        touch "${devnode}"
        mount /dev/"${node}" "${devnode}" -o bind
    done
    ln -s /proc/self/fd/2 cache/root/dev/stderr
    ln -s /proc/self/fd/1 cache/root/dev/stdout
    ln -s /proc/self/fd/0 cache/root/dev/stdin
    ln -s /proc/kcore cache/root/dev/core
    ln -s /proc/self/fd cache/root/dev/fd
    ln -s pts/ptmx cache/root/dev/ptmx
    ln -s $(readlink -f /dev/stdout) cache/root/dev/console
}

enable_network() {
    cat /etc/resolv.conf > cache/resolv.conf
    mount cache/resolv.conf cache/root/etc/resolv.conf -o bind
}

disable_network() {
    umount cache/root/etc/resolv.conf
}

bootstrap_root() {
    bin/pacman -Sy --config cache/pacman-loose.conf --noconfirm "${install_pkgs_bootstrap[@]}"
    echo '[7Ji]
Server = https://github.com/7Ji/archrepo/releases/download/$arch' >> cache/root/etc/pacman.conf
    enable_network
    rm -rf cache/root/etc/pacman.d/gnupg
    # GnuPG > 2.4.3-2 does not like SHA1 only key, which is what ALARM Builder uses
    # :-( Sigh, let's lsign the key manually to give it full trust
    chroot cache/root /bin/bash -c "pacman-key --init && pacman-key --populate && pacman-key --lsign 68B3537F39A313B3E574D06777193F152BDBE6A6"
    disable_network
}

install_mkinitcpio() {
    # This is a huge hack, basically we disable post-transaction hook that would 
    # call mkinitcpio, so mkinitcpio won't be called in target, then we run 
    # mkinitcpio manually, with compression disabled, and also only create
    # fallback initcpio.
    # We then compress the initcpio on host.
    # This avoids the performance penalty if mkinitcpio runs with compression in
    # target, as qemu is not that efficient
    bin/pacman -S --config cache/pacman-strict.conf --noconfirm mkinitcpio
    local mkinitcpio_conf=cache/root/etc/mkinitcpio.conf
    cp "${mkinitcpio_conf}"{,.pacsave}
    echo 'COMPRESSION=cat' >> "${mkinitcpio_conf}"
    local mkinitcpio_install_hook=cache/root/usr/share/libalpm/hooks/90-mkinitcpio-install.hook
    mv "${mkinitcpio_install_hook}"{,.pacsave}
}

install_pkgs() {
    bin/pacman -S --config cache/pacman-strict.conf --noconfirm "${install_pkgs_kernel[@]}" "${install_pkgs_normal[@]}"
}

setup_root() {
    # /etc/fstab
    echo "# root partition with ext4 on SDcard / USB drive
UUID=${uuid_root}	/	ext4	rw,noatime	0 1
# boot partition with vfat on SDcard / USB drive
UUID=${uuid_boot_specifier}	/boot	vfat	rw,noatime	0 2" >>  cache/root/etc/fstab
    # Timezone
    ln -sf "/usr/share/zoneinfo/UTC" cache/root/etc/localtime
    # Locale
    sed -i 's/^#\(en_US.UTF-8  \)$/\1/g' cache/root/etc/locale.gen
    echo 'LANG=en_US.UTF-8' > cache/root/etc/locale.conf

    # Network
    echo alarm > cache/root/etc/hostname
    printf '127.0.0.1\tlocalhost\n::1\t\tlocalhost\n' >> cache/root/etc/hosts
    printf '[Match]\nName=eth* en*\n\n[Network]\nDHCP=yes\nDNSSEC=no\n' > cache/root/etc/systemd/network/20-wired.network

    # Users
    local sudoers=cache/root/etc/sudoers
    chmod o+w "${sudoers}"
    sed -i 's|^# %wheel ALL=(ALL:ALL) ALL$|%wheel ALL=(ALL:ALL) ALL|g' "${sudoers}"
    chmod o-w "${sudoers}"

    # Actual resolv
    ln -sf /run/systemd/resolve/resolv.conf cache/root/etc/resolv.conf

    # Temporary hack before https://gitlab.archlinux.org/archlinux/mkinitcpio/mkinitcpio/-/issues/218 is resolved
    sed -i 's/^HOOKS=(base udev autodetect modconf kms keyboard keymap consolefont block filesystems fsck)$/HOOKS=(base udev autodetect modconf keyboard keymap consolefont block filesystems fsck)/'  cache/root/etc/mkinitcpio.conf

    # Things that need to done inside the root
    chroot cache/root /bin/bash -ec "locale-gen
systemctl enable systemd-{network,resolve,timesync}d usb2host sshd
useradd --groups wheel --create-home --password '"'$y$j9T$raNZsZE8wMTuGo2FHnYBK/$0Z0OEtF62U.wONdo.nyd/GodMLEh62kTdZXeb10.yT7'"' alarm"
}

setup_kernel() {
    if [[ ${#install_pkgs_kernel[@]} == 0 ]]; then
        return
    fi
    local kernel
    for kernel in "${install_pkgs_kernel[@]}"; do
        local preset=cache/root/etc/mkinitcpio.d/"${kernel}".preset
        cp "${preset}"{,.pacsave}
        printf '\nPRESETS=(fallback)\n' >> "${preset}"
    done
    for module_dir in cache/root/usr/lib/modules/*; do
        cp "${module_dir}"/vmlinuz cache/root/boot/vmlinuz-$(<"${module_dir}"/pkgbase)
    done
    chroot cache/root mkinitcpio -P
    # Manually compress
    for kernel in "${install_pkgs_kernel[@]}"; do
        mv cache/root/etc/mkinitcpio.d/"${kernel}".preset{.pacsave,}
        local initramfs=cache/root/boot/initramfs-"${kernel}"-fallback.img
        zstd -T0 "${initramfs}"
        mv "${initramfs}"{.zst,}
    done
}

setup_extlinux() {
    # Setup configuration
    local conf=cache/extlinux.conf
    echo "DEFAULT ${install_pkgs_kernel[0]}" > "${conf}"
    local kernel
    for kernel in "${install_pkgs_kernel[@]}"; do
        printf \
            "LABEL\t%s\n\tLINUX\t/%s\n\tINITRD\t/%s\n\tFDT\t/%s\n\tFDTOVERLAYS\t%s\n\tAPPEND\t%s\n" \
            "${kernel}" \
            "vmlinuz-${kernel}" \
            "initramfs-${kernel}-fallback.img" \
            "dtbs/${kernel}/rockchip/rk3588s-orangepi-5.dtb" \
            "${kernel}" \
            "root=UUID=${uuid_root} rw cma=128M" >> "${conf}"
    done
    sed '/^FDTOVERLAYS\t'"${kernel}"'$/d' "${conf}" |
        install -DTm644 /dev/stdin cache/root/boot/extlinux/extlinux.conf
}

unhack_mkinitcpio() {
    local mkinitcpio_conf=cache/root/etc/mkinitcpio.conf
    local mkinitcpio_install_hook=cache/root/usr/share/libalpm/hooks/90-mkinitcpio-install.hook
    mv "${mkinitcpio_conf}"{.pacsave,}
    mv "${mkinitcpio_install_hook}"{.pacsave,}
}

cleanup_pkgs() {
    bin/pacman -Sc --config cache/pacman-strict.conf --noconfirm
}

umount_root_sub() {
    chroot cache/root killall -s KILL gpg-agent dirmngr || true
    for node in full null random tty urandom zero pts; do
        umount --lazy cache/root/dev/"${node}"
    done
    umount --lazy cache/root/proc
    rm -rf cache/root/dev/*
}

archive_root() {
    local archive=out/"${build_id}"-root.tar
    (
        cd cache/root
        bsdtar --acls --xattrs -cpf - *
    ) > "${archive}".temp
    mv "${archive}"{.temp,}
}

set_parts() {
    spart_size_all=2048
    spart_size_boot=256
    spart_off_root=$(( ${spart_off_boot} + ${spart_size_boot} ))
    spart_size_root=$((  ${spart_size_all} - 1 - ${spart_off_root} ))
}

image_boot() {
    local image=cache/boot.img
    rm -f "${image}"
    truncate -s "${spart_size_boot}"M "${image}"
    mkfs.vfat -n 'ALARMBOOT' -F 32 -i "${uuid_boot_mkfs}" "${image}"
    mcopy -osi "${image}" cache/root/boot/* ::
}

cleanup_boot() {
    rm -rf cache/root/boot/*
}

image_root() {
    local image=cache/root.img
    rm -f "${image}"
    truncate -s "${spart_size_root}"M "${image}"
    mkfs.ext4 -L 'ALARMROOT' -m 0 -U "${uuid_root}" -d cache/root "${image}"
}

work_child() {
    trap "cleanup_child" INT TERM EXIT
    sleep 1
    check_identity_map_root
    mount_root
    bootstrap_root
    install_mkinitcpio
    install_pkgs
    setup_root
    setup_kernel
    setup_extlinux
    unhack_mkinitcpio
    cleanup_pkgs
    umount_root_sub
    archive_root
    set_parts
    image_boot
    cleanup_boot
    image_root
}

arg0="$0"
argv=("$@")
argc=$#

i=0
while [[ $i -lt ${argc} ]]; do
    case "${argv[$i]}" in
        --install)
            i=$(( $i + 1 ))
            install_pkgs_normal+=("${argv[$i]}")
            ;;
        --install-bootstrap)
            i=$(( $i + 1 ))
            install_pkgs_bootstrap+=("${argv[$i]}")
            ;;
        --install-kernel)
            i=$(( $i + 1 ))
            install_pkgs_kernel+=("${argv[$i]}")
            ;;
        --uuid-root)
            i=$(( $i + 1 ))
            uuid_root="${argv[$i]}"
            ;;
        --uuid-boot)
            i=$(( $i + 1 ))
            uuid_boot="${argv[$i]}"
            ;;
        --build-id)
            i=$(( $i + 1 ))
            build_id="${argv[$i]}"
            ;;
        *)
            echo "ERROR: Unknown arg '${argv[$i]}'"
            help
            exit 1
            ;;
    esac
    i=$(( $i + 1 ))
done

uuid_boot_mkfs=''
uuid_boot_specifier=''

uuid_boot_mkfs=${uuid_boot::8}
uuid_boot_mkfs=${uuid_boot_mkfs^^}
uuid_boot_specifier="${uuid_boot_mkfs::4}-${uuid_boot_mkfs:4}"

work_child