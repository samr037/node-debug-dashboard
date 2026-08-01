# Pinned by digest: an unpinned tag means two builds of the same commit can
# produce different images, which is exactly what you do not want when the
# artifact is a debugging tool you compare across nodes.
# debian:bookworm as of 2026-08-01.
FROM debian:bookworm@sha256:9344f8b8992482f80cba753f323adeaf17690076c095ccff6cc9536be98185dc

# All diagnostic tools baked at build time — no runtime apt-get needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    # SSH server
    openssh-server sudo \
    # Shell & terminal
    zsh vim nano less tmux screen bash-completion man-db manpages \
    # Network diagnostics
    curl wget ca-certificates gnupg dnsutils bind9-host \
    net-tools iproute2 iputils-ping iputils-tracepath traceroute \
    mtr-tiny tcpdump tshark nmap ncat socat iperf3 ethtool bridge-utils \
    iptables nftables conntrack iw wireless-tools \
    # Process & performance
    htop btop procps sysstat iotop dstat strace lsof psmisc \
    # Disk & filesystem
    util-linux fdisk gdisk parted lvm2 mdadm smartmontools \
    hdparm nvme-cli fio blktrace e2fsprogs xfsprogs btrfs-progs \
    dosfstools lsscsi sysfsutils \
    # Hardware inspection
    dmidecode lshw pciutils usbutils numactl hwinfo efibootmgr \
    # Stress testing
    stress-ng memtester \
    # Utilities
    jq tree file rsync tar gzip bzip2 xz-utils zstd unzip git \
    openssl bc dc cron at locales fontconfig \
    # Python for the dashboard
    python3 python3-pip python3-venv \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# amd64-only tooling
ARG TARGETARCH
RUN if [ "$TARGETARCH" = "amd64" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends cpuid \
      && apt-get clean && rm -rf /var/lib/apt/lists/*; \
    fi

# Generate locale for proper Unicode rendering
RUN sed -i '/en_US.UTF-8/s/^# //' /etc/locale.gen && locale-gen
ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

# CRI tools (crictl) for container inspection
ARG CRICTL_VERSION=v1.32.0
RUN curl -fsSL "https://github.com/kubernetes-sigs/cri-tools/releases/download/${CRICTL_VERSION}/crictl-${CRICTL_VERSION}-linux-${TARGETARCH}.tar.gz" \
    | tar -xz -C /usr/local/bin \
    && chmod +x /usr/local/bin/crictl \
    && printf 'runtime-endpoint: unix:///host/run/containerd/containerd.sock\nimage-endpoint: unix:///host/run/containerd/containerd.sock\n' > /etc/crictl.yaml

# Oh-My-Zsh — shared install for all users, pinned to specific commits.
# These were cloned from master at build time, so the shell environment
# shipped in a "reproducible" image changed whenever upstream pushed.
ARG OHMYZSH_REF=c5ba74cf02cce4c342153f79089100194f30940f
ARG ZSH_AUTOSUGGESTIONS_REF=85919cd1ffa7d2d5412f6d3fe437ebdbeeec4fc5
ARG ZSH_SYNTAX_HIGHLIGHTING_REF=1d85c692615a25fe2293bdd44b34c217d5d2bf04
RUN set -eux; \
    clone_at() { \
        git clone --quiet "$1" "$2"; \
        git -C "$2" checkout --quiet "$3"; \
        rm -rf "$2/.git"; \
    }; \
    clone_at https://github.com/ohmyzsh/ohmyzsh.git /opt/oh-my-zsh "$OHMYZSH_REF"; \
    clone_at https://github.com/zsh-users/zsh-autosuggestions /opt/oh-my-zsh/custom/plugins/zsh-autosuggestions "$ZSH_AUTOSUGGESTIONS_REF"; \
    clone_at https://github.com/zsh-users/zsh-syntax-highlighting /opt/oh-my-zsh/custom/plugins/zsh-syntax-highlighting "$ZSH_SYNTAX_HIGHLIGHTING_REF"

# Python application
WORKDIR /opt/node-dashboard

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY app/ app/
COPY entrypoint.sh ssh_config.conf ./
COPY ssh/vimrc /etc/vim/vimrc.local
COPY ssh/zshrc /etc/zsh/zshrc.local
COPY ssh/motd.sh /etc/profile.d/motd.sh
COPY ssh/completions/ /opt/node-dashboard/ssh/completions/
COPY scripts/ /opt/node-dashboard/scripts/
RUN chmod +x entrypoint.sh /etc/profile.d/motd.sh /opt/node-dashboard/scripts/*

# SSH & user setup
RUN mkdir -p /run/sshd && \
    # Create debug user with zsh. Both accounts are left locked: a published
    # image with a known password is a credential anyone can look up, and
    # this one grants root on the node. Set SSH_PASSWORD at runtime if you
    # genuinely need password auth; otherwise use SSH_AUTHORIZED_KEYS.
    useradd -m -s /bin/zsh -G sudo debug && \
    passwd -l debug && \
    passwd -l root && \
    chsh -s /bin/zsh root && \
    echo "debug ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/debug && \
    cp ssh_config.conf /etc/ssh/sshd_config.d/debug.conf && \
    # Disable default MOTD
    chmod -x /etc/update-motd.d/* 2>/dev/null || true && \
    rm -f /etc/motd && \
    # Vim config for both users
    cp /etc/vim/vimrc.local /root/.vimrc && \
    cp /etc/vim/vimrc.local /home/debug/.vimrc && \
    chown debug:debug /home/debug/.vimrc && \
    # Zsh config for both users — source the shared config
    echo 'source /etc/zsh/zshrc.local' > /root/.zshrc && \
    echo 'source /etc/zsh/zshrc.local' > /home/debug/.zshrc && \
    chown debug:debug /home/debug/.zshrc && \
    # Completions
    mkdir -p /usr/local/share/zsh/site-functions && \
    cp /opt/node-dashboard/ssh/completions/* /usr/local/share/zsh/site-functions/ && \
    # Ensure scripts are in PATH
    ln -sf /opt/node-dashboard/scripts/ndiag-cpu /usr/local/bin/ndiag-cpu && \
    ln -sf /opt/node-dashboard/scripts/ndiag-mem /usr/local/bin/ndiag-mem && \
    ln -sf /opt/node-dashboard/scripts/ndiag-net /usr/local/bin/ndiag-net && \
    ln -sf /opt/node-dashboard/scripts/ndiag-disk /usr/local/bin/ndiag-disk && \
    ln -sf /opt/node-dashboard/scripts/ndiag-part /usr/local/bin/ndiag-part && \
    ln -sf /opt/node-dashboard/scripts/kdiag-node /usr/local/bin/kdiag-node && \
    ln -sf /opt/node-dashboard/scripts/kdiag-pods /usr/local/bin/kdiag-pods && \
    ln -sf /opt/node-dashboard/scripts/kdiag-etcd /usr/local/bin/kdiag-etcd && \
    ln -sf /opt/node-dashboard/scripts/kdiag-certs /usr/local/bin/kdiag-certs && \
    ln -sf /opt/node-dashboard/scripts/kdiag-services /usr/local/bin/kdiag-services && \
    ln -sf /opt/node-dashboard/scripts/kdiag-events /usr/local/bin/kdiag-events && \
    ln -sf /opt/node-dashboard/scripts/ndiag-test /usr/local/bin/ndiag-test && \
    ln -sf /opt/node-dashboard/scripts/ndiag-fsck /usr/local/bin/ndiag-fsck && \
    ln -sf /opt/node-dashboard/scripts/ndiag-memtest /usr/local/bin/ndiag-memtest && \
    ln -sf /opt/node-dashboard/scripts/ndiag-efi /usr/local/bin/ndiag-efi && \
    # Allow tshark to be run without root (for debug user)
    setcap cap_net_raw,cap_net_admin+eip /usr/bin/dumpcap 2>/dev/null || true

EXPOSE 80 2022

ENTRYPOINT ["/opt/node-dashboard/entrypoint.sh"]
