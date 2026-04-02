# Raspberry Pi SD Card Longevity Tweaks

## /etc/fstab changes

Add tmpfs mounts and `commit=600` to the root partition:

```
PARTUUID=0e8c0e46-02  /               ext4    defaults,noatime,commit=600  0       1
tmpfs /tmp     tmpfs defaults,noatime,size=50M 0 0
tmpfs /var/log tmpfs defaults,noatime,size=30M 0 0
```

- `noatime` — prevents writing access timestamps on every file read
- `commit=600` — delays syncing dirty data to every 10 minutes (default is 5 seconds)
- `tmpfs` — keeps `/tmp` and `/var/log` in RAM, eliminating write-heavy temp and log I/O

**Note:** `/var/log` in tmpfs means logs are lost on reboot. Skip that line if you need persistent `journalctl` history.

## Disable swap

```bash
sudo dphys-swapfile swapoff
sudo systemctl disable dphys-swapfile
```

Prevents swap writes to the SD card. Fine as long as memory usage stays within available RAM.

## Apply and reboot

```bash
sudo reboot
```

## Health check

```bash
vcgencmd measure_temp        # should be under 70°C
vcgencmd get_throttled       # 0x0 = no issues
free -h                      # check available memory
df -h /                      # check disk usage
dmesg | grep -i "i/o error"  # check for SD card errors
```
