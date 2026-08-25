#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="$(tr -d '[:space:]' < "$project_dir/VERSION")"
package_path="${1:-$project_dir/release/nju-hpc-sync_${version}_amd64.deb}"

if [[ ! -f "$package_path" ]]; then
    echo "找不到 deb：$package_path" >&2
    exit 1
fi

package_path="$(realpath "$package_path")"

for ubuntu_version in 24.04 26.04; do
    echo "验证 Ubuntu $ubuntu_version ..."
    docker run --rm \
        --platform linux/amd64 \
        --env DEBIAN_FRONTEND=noninteractive \
        --volume "$package_path:/tmp/nju-hpc-sync.deb:ro" \
        "public.ecr.aws/ubuntu/ubuntu:$ubuntu_version" \
        bash -euc '
            apt-get update -qq
            apt-get install -y -qq /tmp/nju-hpc-sync.deb >/dev/null
            set +e
            QT_QPA_PLATFORM=offscreen timeout 5s nju-hpc-sync
            launch_status=$?
            set -e
            if [[ "$launch_status" -ne 124 ]]; then
                echo "NJU-HPC Sync 启动失败，退出码：$launch_status" >&2
                exit 1
            fi
            dpkg-query -W nju-hpc-sync rsync openssh-client
        '
done

echo "Ubuntu 24.04/26.04 冒烟测试通过"
