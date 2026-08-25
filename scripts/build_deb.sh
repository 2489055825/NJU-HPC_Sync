#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_python="${NJU_HPC_BUILD_PYTHON:-python3}"
version="$(tr -d '[:space:]' < "$project_dir/VERSION")"
architecture="$(dpkg --print-architecture)"
release_dir="$project_dir/release"

if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([+~-][0-9A-Za-z.+~-]+)?$ ]]; then
    echo "VERSION 格式无效：$version" >&2
    exit 1
fi

if [[ "$architecture" != "amd64" ]]; then
    echo "当前只支持在 amd64 环境构建，检测到：$architecture" >&2
    exit 1
fi

if [[ ! -r /etc/os-release ]]; then
    echo "无法确认构建系统版本" >&2
    exit 1
fi

source /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "22.04" ]]; then
    echo "请在 Ubuntu 22.04 amd64 环境构建；当前为 ${PRETTY_NAME:-unknown}" >&2
    exit 1
fi

if ! "$build_python" -m PyInstaller --version >/dev/null 2>&1; then
    echo "缺少 PyInstaller，请先执行：$build_python -m pip install -r build-requirements.txt" >&2
    exit 1
fi

temporary_dir="$(mktemp -d -t nju-hpc-sync-build.XXXXXXXX)"
trap 'rm -rf -- "$temporary_dir"' EXIT

"$build_python" -m PyInstaller \
    --noconfirm \
    --clean \
    --distpath "$temporary_dir/dist" \
    --workpath "$temporary_dir/build" \
    "$project_dir/nju-hpc-sync.spec"

# A conda Python can use a newer ICU than Ubuntu 22.04's libstdc++ supports.
# Keep the packaged C++ runtime from the same Python environment when present.
python_prefix="$("$build_python" -c 'import sys; print(sys.prefix)')"
for runtime_library in libstdc++.so.6 libgcc_s.so.1; do
    runtime_source="$python_prefix/lib/$runtime_library"
    if [[ -f "$runtime_source" ]]; then
        install -m 0755 \
            "$runtime_source" \
            "$temporary_dir/dist/nju-hpc-sync/_internal/$runtime_library"
    fi
done

package_root="$temporary_dir/package"
install -d \
    "$package_root/DEBIAN" \
    "$package_root/opt/nju-hpc-sync" \
    "$package_root/usr/bin" \
    "$package_root/usr/share/applications" \
    "$package_root/usr/share/doc/nju-hpc-sync"

cp -a "$temporary_dir/dist/nju-hpc-sync/." "$package_root/opt/nju-hpc-sync/"
chmod -R go-w "$package_root/opt/nju-hpc-sync"
install -m 0755 "$project_dir/packaging/linux/nju-hpc-sync" "$package_root/usr/bin/nju-hpc-sync"
install -m 0644 "$project_dir/packaging/linux/nju-hpc-sync.desktop" "$package_root/usr/share/applications/nju-hpc-sync.desktop"
install -m 0644 "$project_dir/README.md" "$package_root/usr/share/doc/nju-hpc-sync/README.md"

for size in 128 256 512; do
    icon_dir="$package_root/usr/share/icons/hicolor/${size}x${size}/apps"
    install -d "$icon_dir"
    install -m 0644 \
        "$project_dir/packaging/linux/icons/hicolor/${size}x${size}/apps/nju-hpc-sync.png" \
        "$icon_dir/nju-hpc-sync.png"
done

sed \
    -e "s/@VERSION@/$version/g" \
    -e "s/@ARCH@/$architecture/g" \
    "$project_dir/packaging/linux/control.in" > "$package_root/DEBIAN/control"

install -d "$release_dir"
package_name="nju-hpc-sync_${version}_${architecture}.deb"
temporary_package="$temporary_dir/$package_name"
dpkg-deb --build --root-owner-group "$package_root" "$temporary_package"
install -m 0644 "$temporary_package" "$release_dir/$package_name"
(
    cd "$release_dir"
    sha256sum "$package_name" > "$package_name.sha256"
    chmod 0644 "$package_name.sha256"
)

echo "构建完成：$release_dir/$package_name"
echo "校验文件：$release_dir/$package_name.sha256"
