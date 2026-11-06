#!/usr/bin/env bash
# Optional: this project's actual WSL custom kernel needs these modules loaded explicitly.
# Run as root before `docker compose up`; does not rewrite Docker daemon settings.
set -euo pipefail
if ! grep -qi microsoft /proc/version && [[ "$(uname -r)" != *shiruijie* ]]; then
    echo 'This helper is only intended for WSL. Use your normal Docker installation otherwise.' >&2
    exit 1
fi
for module in bridge nf_conntrack nf_nat xt_nat iptable_nat xt_MASQUERADE xt_tcpudp br_netfilter nft_compat nft_chain_nat; do
    modprobe "$module"
done
sysctl -w net.ipv4.ip_forward=1
echo 'WSL Docker bridge and netfilter modules are ready for this boot.'
