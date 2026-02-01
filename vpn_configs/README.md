# VPN Configuration Setup

## ProtonVPN OpenVPN Configs

1. Log in to ProtonVPN: https://account.protonvpn.com/downloads
2. Go to "OpenVPN configuration files"
3. Select "Router" platform
4. Download configs for each country:
   - US.ovpn (United States)
   - NL.ovpn (Netherlands)
   - JP.ovpn (Japan)
5. Place the .ovpn files in this directory

## Credentials

Create a file called `credentials.txt` in this directory with:
```
your_protonvpn_username
your_protonvpn_password
```

Note: Use your OpenVPN/IKEv2 credentials, not your account password.
Find them at: https://account.protonvpn.com/account#openvpn

## Required Files

```
vpn_configs/
  US.ovpn
  NL.ovpn
  JP.ovpn
  credentials.txt
  README.md (this file)
```
