#!/bin/bash
# optimize_power.sh — disable unneeded services & enable only what we need

# 1) Basic setup
USER_HOME="/home/$(whoami)"
DESKTOP="$USER_HOME/Desktop"

echo "=== Optimizing power & disabling services ==="

# 2) Enable required interfaces
echo "-- Enabling SSH, I2C, SPI, camera"
sudo systemctl enable ssh
sudo raspi-config nonint do_ssh 0
sudo raspi-config nonint do_i2c 0
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_camera 0

# 3) Install necessary packages
echo "-- Installing Python & camera tools"
sudo apt-get update -qq
sudo apt-get install -y python3-pip python3-smbus i2c-tools git libcamera-apps

# 4) Install Python dependencies
echo "-- Installing Python dependencies"
pip3 install --user -r "$DESKTOP/requirements.txt"

# 5) Disable unneeded services
SERVICES=(
  bluetooth.service
  hciuart.service
  avahi-daemon.service
  triggerhappy.service
  cron.service
  cups.service
  alsa-restore.service
  alsa-state.service
  modemmanager.service
)
echo "-- Disabling services: ${SERVICES[*]}"
for s in "${SERVICES[@]}"; do
  sudo systemctl stop   "$s" 2>/dev/null
  sudo systemctl disable "$s" 2>/dev/null
done

# 6) Tweak /boot/config.txt
echo "-- Tweaking /boot/config.txt"
sudo sed -i 's/^dtparam=audio=on/#dtparam=audio=on/' /boot/config.txt
sudo sed -i 's/^#disable_splash=.*/disable_splash=1/' /boot/config.txt || \
  sudo tee -a /boot/config.txt <<< "disable_splash=1"
sudo tee -a /boot/config.txt <<EOF

# Power-save tweaks
hdmi_blanking=2          # turn HDMI off
disable_overscan=1       # no overscan
arm_freq=1000            # lower CPU freq
core_freq=250
temp_limit=70
disable_splash=1
EOF

# 7) Turn off HDMI right now
echo "-- Turning off HDMI"
sudo /usr/bin/tvservice -o

# 8) Set CPU governor to powersave
echo "-- Setting CPU governor to powersave"
sudo apt-get install -y cpufrequtils
echo 'GOVERNOR="powersave"' | sudo tee /etc/default/cpufrequtils
sudo systemctl restart cpufrequtils

# 9) Swap and logging
echo "-- Reducing filesystem writes"
sudo systemctl mask rsyslog.service
sudo systemctl mask dphys-swapfile.service
sudo systemctl stop  dphys-swapfile.service

# 10) Done
echo "=== Done. Rebooting in 5s ==="
sleep 5
sudo reboot
