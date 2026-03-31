FROM debian:bookworm

# All diagnostic tools baked at build time — no runtime apt-get needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    # SSH server
    openssh-server sudo \
    # Editors & shell
    vim nano less tmux screen bash-completion man-db manpages \
    # Network diagnostics
    curl wget ca-certificates gnupg dnsutils bind9-host \
    net-tools iproute2 iputils-ping iputils-tracepath traceroute \
    mtr-tiny tcpdump nmap ncat socat iperf3 ethtool bridge-utils \
    iptables nftables conntrack iw wireless-tools \
    # Process & performance
    htop btop procps sysstat iotop dstat strace ltrace lsof psmisc \
    # Disk & filesystem
    util-linux fdisk gdisk parted lvm2 mdadm smartmontools \
    hdparm nvme-cli fio blktrace e2fsprogs xfsprogs btrfs-progs \
    dosfstools lsscsi sysfsutils \
    # Hardware inspection
    dmidecode lshw pciutils usbutils cpuid numactl hwinfo efibootmgr \
    # Stress testing
    stress-ng memtester \
    # Utilities
    jq tree file rsync tar gzip bzip2 xz-utils zstd unzip git \
    openssl bc dc cron at \
    # Python for the dashboard
    python3 python3-pip python3-venv \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# CRI tools (crictl) for container inspection
ARG CRICTL_VERSION=v1.32.0
RUN curl -fsSL "https://github.com/kubernetes-sigs/cri-tools/releases/download/${CRICTL_VERSION}/crictl-${CRICTL_VERSION}-linux-amd64.tar.gz" \
    | tar -xz -C /usr/local/bin crictl \
    && chmod +x /usr/local/bin crictl

# Python application
WORKDIR /opt/node-dashboard

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY app/ app/
COPY entrypoint.sh ssh_config.conf ./
RUN chmod +x entrypoint.sh

# SSH & user setup
RUN mkdir -p /run/sshd && \
    useradd -m -s /bin/bash -G sudo debug && \
    echo "debug:debug" | chpasswd && \
    echo "root:root" | chpasswd && \
    echo "debug ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/debug && \
    cp ssh_config.conf /etc/ssh/sshd_config.d/debug.conf

EXPOSE 80 2022

ENTRYPOINT ["/opt/node-dashboard/entrypoint.sh"]
