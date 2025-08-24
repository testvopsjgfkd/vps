import os
import time
import requests
import json
import subprocess
from github import Github
import re
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
import logging
from github.GithubException import UnknownObjectException

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

NGROK_TOKEN_LOCAL = "2xUNmo23j31XVph9fkNY6HPQYvW_5uAZZbzhVUy9aEd5ajGTp"
ALLOWED_ORIGIN_PATTERN = r"https?://([a-zA-Z0-9-]+\.)?ducknovis\.site(/.*)?$"
LOCAL_PORT = 2612
VPS_USER_FILE = "vpsuser.txt"

app = Flask(__name__)
CORS(app, origins="*")

def load_vps_users():
    users = {}
    if os.path.exists(VPS_USER_FILE):
        try:
            with open(VPS_USER_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and '|' in line:
                        token, link = line.split('|', 1)
                        users[token] = link
        except Exception as e:
            logging.error(f"Lỗi khi đọc file {VPS_USER_FILE}: {e}")
    return users

def save_vps_user(github_token, remote_link):
    users = load_vps_users()
    users[github_token] = remote_link
    
    try:
        with open(VPS_USER_FILE, 'w', encoding='utf-8') as f:
            for token, link in users.items():
                f.write(f"{token}|{link}\n")
        logging.info(f"Đã lưu VPS user: {github_token[:10]}...***")
    except Exception as e:
        logging.error(f"Lỗi khi lưu file {VPS_USER_FILE}: {e}")
import logging

def generate_tmate_yml(github_token, ngrok_server_url, vps_name, repo_full_name):
    logging.debug("Tạo nội dung tmate.yml...")
    return fr"""
name: Create VPS (Auto Restart)

on:
  workflow_dispatch:
  repository_dispatch:
    types: [create-vps]

env:
  VPS_NAME: {vps_name}
  TMATE_SERVER: nyc1.tmate.io
  GITHUB_TOKEN_VPS: {github_token}
  NGROK_SERVER_URL: {ngrok_server_url}

jobs:
  deploy:
    runs-on: windows-latest
    permissions:
      contents: write
      actions: write

    steps:
    - name: ⬇️ Checkout source
      uses: actions/checkout@v4
      with:
        token: {github_token}

    - name: 🐍 Tạo file VPS info
      run: |
        mkdir -Force links
        "VPS khởi tạo - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -FilePath "links/{vps_name}.txt" -Encoding UTF8

    - name: 🖥️ Cài đặt và chạy TightVNC, noVNC, Cloudflared
      shell: pwsh
      run: |
        Write-Host "📥 Installing TightVNC, noVNC, and Cloudflared..."
        
        try {{
          Write-Host "📥 Installing TightVNC..."
          Invoke-WebRequest -Uri "https://www.tightvnc.com/download/2.8.63/tightvnc-2.8.63-gpl-setup-64bit.msi" -OutFile "tightvnc-setup.msi" -TimeoutSec 60
          Write-Host "✅ TightVNC downloaded"
          
          Start-Process msiexec.exe -Wait -ArgumentList '/i tightvnc-setup.msi /quiet /norestart ADDLOCAL="Server" SERVER_REGISTER_AS_SERVICE=1 SERVER_ADD_FIREWALL_EXCEPTION=1 SET_USEVNCAUTHENTICATION=1 VALUE_OF_USEVNCAUTHENTICATION=1 SET_PASSWORD=1 VALUE_OF_PASSWORD=ducknovis SET_ACCEPTHTTPCONNECTIONS=1 VALUE_OF_ACCEPTHTTPCONNECTIONS=1 SET_ALLOWLOOPBACK=1 VALUE_OF_ALLOWLOOPBACK=1'
          Write-Host "✅ TightVNC installed"
          
          Write-Host "🔧 Enabling loopback connections in TightVNC registry..."
          Set-ItemProperty -Path "HKLM:\SOFTWARE\TightVNC\Server" -Name "AllowLoopback" -Value 1 -ErrorAction SilentlyContinue
          
          Write-Host "🔍 Stopping any existing tvnserver processes..."
          Stop-Process -Name "tvnserver" -Force -ErrorAction SilentlyContinue
          Stop-Service -Name "tvnserver" -Force -ErrorAction SilentlyContinue
          Start-Sleep -Seconds 5
          
          Write-Host "🔍 Checking for port 5900 conflicts..."
          $portCheck = netstat -aon | FindStr :5900
          if ($portCheck) {{
            Write-Host "⚠️ Port 5900 is already in use: $portCheck"
            Stop-Process -Id ($portCheck -split '\s+')[-1] -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 5
          }}
          
          Write-Host "🚀 Starting TightVNC server..."
          Start-Process -FilePath "C:\Program Files\TightVNC\tvnserver.exe" -ArgumentList "-run -localhost no" -WindowStyle Hidden -RedirectStandardOutput "vnc_start.log" -RedirectStandardError "vnc_error.log"
          Start-Sleep -Seconds 40
          Get-Content "vnc_start.log" -ErrorAction SilentlyContinue | Write-Host
          Get-Content "vnc_error.log" -ErrorAction SilentlyContinue | Write-Host
          
          netsh advfirewall firewall add rule name="Allow VNC 5900" dir=in action=allow protocol=TCP localport=5900
          netsh advfirewall firewall add rule name="Allow noVNC 6080" dir=in action=allow protocol=TCP localport=6080
          Write-Host "✅ Firewall rules added"
          
          Write-Host "📥 Installing Python dependencies for noVNC and websockify..."
          Write-Host "🔍 Checking Python and pip versions..."
          python --version | Write-Host
          python -m pip --version | Write-Host
          
          $maxPipAttempts = 5
          for ($i = 1; $i -le $maxPipAttempts; $i++) {{
            try {{
              python -m pip install --upgrade pip --timeout 60 2>&1 | Out-File -FilePath "pip_install.log" -Append -Encoding UTF8
              pip install --force-reinstall numpy novnc websockify==0.13.0 --timeout 60 2>&1 | Out-File -FilePath "pip_install.log" -Append -Encoding UTF8
              Write-Host "✅ Python dependencies installed"
              break
            }} catch {{
              Write-Host "⚠️ Pip install attempt $i/$maxPipAttempts failed: $_"
              Get-Content "pip_install.log" -ErrorAction SilentlyContinue | Write-Host
              if ($i -eq $maxPipAttempts) {{
                Write-Host "❌ Failed to install Python dependencies"
                exit 1
              }}
              Start-Sleep -Seconds 10
            }}
          }}
          
          Write-Host "🔍 Checking noVNC installation via pip..."
          try {{
            $novncInfo = pip show novnc
            Write-Host "📜 noVNC package info:"
            Write-Host $novncInfo
            $novncPath = ($novncInfo | Select-String "Location: (.*)").Matches.Groups[1].Value + "\novnc"
            if (Test-Path "$novncPath") {{
              dir $novncPath -Recurse | Write-Host
              if (-not (Test-Path "$novncPath/vnc.html")) {{
                Write-Host "❌ noVNC directory is incomplete, vnc.html not found"
                Write-Host "🔄 Falling back to GitHub download..."
                $novncVersion = "v1.6.0"
                $maxDownloadAttempts = 5
                for ($i = 1; $i -le $maxDownloadAttempts; $i++) {{
                  try {{
                    Write-Host "📥 Downloading noVNC release $novncVersion (attempt $i/$maxDownloadAttempts)..."
                    Remove-Item -Recurse -Force noVNC -ErrorAction SilentlyContinue
                    $novncUrl = "https://github.com/novnc/noVNC/archive/refs/tags/$novncVersion.zip"
                    Write-Host "🔗 Using URL: $novncUrl"
                    $response = Invoke-WebRequest -Uri $novncUrl -OutFile "noVNC.zip" -TimeoutSec 60 -PassThru
                    Write-Host "ℹ️ HTTP Status: $($response.StatusCode) $($response.StatusDescription)"
                    Expand-Archive -Path "noVNC.zip" -DestinationPath "." -Force
                    Move-Item -Path "noVNC-$($novncVersion.Substring(1))" -Destination "noVNC" -Force
                    Write-Host "✅ noVNC downloaded and extracted"
                    $novncPath = "noVNC"
                    break
                  }} catch {{
                    Write-Host "⚠️ noVNC download attempt $i/$maxDownloadAttempts failed: $_"
                    if ($i -eq $maxDownloadAttempts) {{
                      Write-Host "❌ Failed to download noVNC"
                      exit 1
                    }}
                    Start-Sleep -Seconds 10
                  }}
                }}
              }}
            }} else {{
              Write-Host "❌ noVNC directory does not exist, falling back to GitHub download..."
              $novncVersion = "v1.6.0"
              $maxDownloadAttempts = 5
              for ($i = 1; $i -le $maxDownloadAttempts; $i++) {{
                try {{
                  Write-Host "📥 Downloading noVNC release $novncVersion (attempt $i/$maxDownloadAttempts)..."
                  Remove-Item -Recurse -Force noVNC -ErrorAction SilentlyContinue
                  $novncUrl = "https://github.com/novnc/noVNC/archive/refs/tags/$novncVersion.zip"
                  Write-Host "🔗 Using URL: $novncUrl"
                  $response = Invoke-WebRequest -Uri $novncUrl -OutFile "noVNC.zip" -TimeoutSec 60 -PassThru
                  Write-Host "ℹ️ HTTP Status: $($response.StatusCode) $($response.StatusDescription)"
                  Expand-Archive -Path "noVNC.zip" -DestinationPath "." -Force
                  Move-Item -Path "noVNC-$($novncVersion.Substring(1))" -Destination "noVNC" -Force
                  Write-Host "✅ noVNC downloaded and extracted"
                  $novncPath = "noVNC"
                  break
                }} catch {{
                  Write-Host "⚠️ noVNC download attempt $i/$maxDownloadAttempts failed: $_"
                  if ($i -eq $maxDownloadAttempts) {{
                    Write-Host "❌ Failed to download noVNC"
                    exit 1
                  }}
                  Start-Sleep -Seconds 10
                }}
              }}
            }}
          }} catch {{
            Write-Host "⚠️ Failed to check noVNC package via pip, falling back to GitHub download..."
            $novncVersion = "v1.6.0"
            $maxDownloadAttempts = 5
            for ($i = 1; $i -le $maxDownloadAttempts; $i++) {{
              try {{
                Write-Host "📥 Downloading noVNC release $novncVersion (attempt $i/$maxDownloadAttempts)..."
                Remove-Item -Recurse -Force noVNC -ErrorAction SilentlyContinue
                $novncUrl = "https://github.com/novnc/noVNC/archive/refs/tags/$novncVersion.zip"
                Write-Host "🔗 Using URL: $novncUrl"
                $response = Invoke-WebRequest -Uri $novncUrl -OutFile "noVNC.zip" -TimeoutSec 60 -PassThru
                Write-Host "ℹ️ HTTP Status: $($response.StatusCode) $($response.StatusDescription)"
                Expand-Archive -Path "noVNC.zip" -DestinationPath "." -Force
                Move-Item -Path "noVNC-$($novncVersion.Substring(1))" -Destination "noVNC" -Force
                Write-Host "✅ noVNC downloaded and extracted"
                $novncPath = "noVNC"
                break
              }} catch {{
                Write-Host "⚠️ noVNC download attempt $i/$maxDownloadAttempts failed: $_"
                if ($i -eq $maxDownloadAttempts) {{
                  Write-Host "❌ Failed to download noVNC"
                  exit 1
                }}
                Start-Sleep -Seconds 10
              }}
            }}
          }}
          
          Write-Host "🔍 Checking noVNC directory structure..."
          if (-not (Test-Path "$novncPath/vnc.html")) {{
            Write-Host "❌ noVNC directory is incomplete, vnc.html not found"
            exit 1
          }}
          
          Write-Host "🔍 Checking for port 6080 conflicts..."
          $portCheck = netstat -aon | FindStr :6080
          if ($portCheck) {{
            Write-Host "⚠️ Port 6080 is already in use: $portCheck"
            Stop-Process -Id ($portCheck -split '\s+')[-1] -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 5
          }}
          
          Write-Host "🚀 Starting websockify..."
          Start-Process -FilePath "python" -ArgumentList "-m", "websockify", "6080", "127.0.0.1:5900", "--web", "$novncPath", "--verbose" -RedirectStandardOutput "websockify.log" -RedirectStandardError "websockify_error.log" -NoNewWindow -PassThru
          Start-Sleep -Seconds 15
          Get-Content "websockify.log" -ErrorAction SilentlyContinue | Write-Host
          Get-Content "websockify_error.log" -ErrorAction SilentlyContinue | Write-Host
          
          Write-Host "📥 Installing Cloudflared..."
          Invoke-WebRequest -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile "cloudflared.exe" -TimeoutSec 60
          Write-Host "✅ Cloudflared downloaded"
          
          Write-Host "🌐 Starting Cloudflared tunnel..."
          Start-Process -FilePath "cloudflared.exe" -ArgumentList "tunnel", "--url", "http://localhost:6080", "--no-autoupdate", "--edge-ip-version", "auto", "--protocol", "http2", "--logfile", "cloudflared.log" -WindowStyle Hidden
          Start-Sleep -Seconds 40
          Get-Content "cloudflared.log" -ErrorAction SilentlyContinue | Write-Host
          
          Write-Host "🚀 Checking noVNC and retrieving Cloudflared URL..."
          
          Write-Host "🔍 Checking for port 5900 and 6080 conflicts..."
          netstat -aon | FindStr :5900 | Write-Host
          netstat -aon | FindStr :6080 | Write-Host
          
          Write-Host "🔍 Checking VNC and websockify processes..."
          Get-Process -Name "tvnserver" -ErrorAction SilentlyContinue | Format-Table -Property Name, Id, StartTime
          Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object {{ $_.CommandLine -like "*websockify*" }} | Format-Table -Property Name, Id, StartTime
          
          $vncReady = $false
          for ($i = 1; $i -le 30; $i++) {{
            try {{
              $tcpConnection = Test-NetConnection -ComputerName "localhost" -Port 5900 -WarningAction SilentlyContinue
              if ($tcpConnection.TcpTestSucceeded) {{
                try {{
                  $vncTest = New-Object System.Net.Sockets.TcpClient
                  $vncTest.Connect("127.0.0.1", 5900)
                  Write-Host "✅ VNC server accepting connections"
                  $vncTest.Close()
                  $vncReady = $true
                  break
                }} catch {{
                  Write-Host "❌ VNC server not accepting connections: $_"
                  Get-Content "vnc_error.log" -ErrorAction SilentlyContinue | Write-Host
                }}
              }}
            }} catch {{
              Write-Host "⚠️ VNC connection test failed: $_"
            }}
            Write-Host "⏳ Waiting for VNC server... ($i/30)"
            
            if ($i % 10 -eq 0) {{
              Write-Host "🔄 Restarting VNC server..."
              Stop-Process -Name "tvnserver" -Force -ErrorAction SilentlyContinue
              Start-Sleep -Seconds 5
              Start-Process -FilePath "C:\Program Files\TightVNC\tvnserver.exe" -ArgumentList "-run -localhost no" -WindowStyle Hidden -RedirectStandardOutput "vnc_start.log" -RedirectStandardError "vnc_error.log"
              Start-Sleep -Seconds 40
              Get-Content "vnc_start.log" -ErrorAction SilentlyContinue | Write-Host
              Get-Content "vnc_error.log" -ErrorAction SilentlyContinue | Write-Host
            }}
            Start-Sleep -Seconds 2
          }}
          
          if (-not $vncReady) {{
            Write-Host "❌ VNC server not ready, exiting..."
            Get-Content "vnc_error.log" -ErrorAction SilentlyContinue | Write-Host
            exit 1
          }}
          
          $websockifyReady = $false
          for ($i = 1; $i -le 3; $i++) {{
            try {{
              $response = Invoke-WebRequest -Uri "http://localhost:6080/vnc.html" -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
              Write-Host "✅ noVNC web interface accessible"
              $websockifyReady = $true
              break
            }} catch {{
              Write-Host "⚠️ noVNC check failed (attempt $i/3): $_"
              Get-Content "websockify.log" -ErrorAction SilentlyContinue | Write-Host
              Get-Content "websockify_error.log" -ErrorAction SilentlyContinue | Write-Host
              Stop-Process -Name "python" -Force -ErrorAction SilentlyContinue
              Start-Sleep -Seconds 5
              Start-Process -FilePath "python" -ArgumentList "-m", "websockify", "6080", "127.0.0.1:5900", "--web", "$novncPath", "--verbose" -RedirectStandardOutput "websockify.log" -RedirectStandardError "websockify_error.log" -NoNewWindow -PassThru
              Start-Sleep -Seconds 15
            }}
          }}
          
          if (-not $websockifyReady) {{
            Write-Host "❌ Failed to start noVNC, exiting..."
            Get-Content "websockify.log" -ErrorAction SilentlyContinue | Write-Host
            Get-Content "websockify_error.log" -ErrorAction SilentlyContinue | Write-Host
            exit 1
          }}
          
          Write-Host "🌐 Retrieving Cloudflared URL..."
          $maxAttempts = 180
          $attempt = 0
          $cloudflaredUrl = ""
          
          do {{
            $attempt++
            Write-Host "🔄 Checking Cloudflared URL (attempt $attempt/$maxAttempts)"
            Start-Sleep -Seconds 3
            
            if (Test-Path "cloudflared.log") {{
              try {{
                $logContent = Get-Content "cloudflared.log" -Raw -ErrorAction SilentlyContinue
                if ($logContent -match 'https://[a-zA-Z0-9-]+\.trycloudflare\.com') {{
                  $cloudflaredUrl = $matches[0]
                  Write-Host "✅ Found Cloudflared URL: $cloudflaredUrl"
                  break
                }}
              }} catch {{
                Write-Host "⚠️ Error reading cloudflared.log: $_"
              }}
            }}
            
            if ($attempt % 20 -eq 0) {{
              Write-Host "🔄 Restarting Cloudflared..."
              Stop-Process -Name "cloudflared" -Force -ErrorAction SilentlyContinue
              Start-Sleep -Seconds 3
              Start-Process -FilePath "cloudflared.exe" -ArgumentList "tunnel", "--url", "http://localhost:6080", "--no-autoupdate", "--edge-ip-version", "auto", "--protocol", "http2", "--logfile", "cloudflared.log" -WindowStyle Hidden
              Start-Sleep -Seconds 40
              Get-Content "cloudflared.log" -ErrorAction SilentlyContinue | Write-Host
            }}
          }} while ($attempt -lt $maxAttempts)
          
          if ($cloudflaredUrl) {{
            $remoteLink = "$cloudflaredUrl/vnc.html"
            Write-Host "🌌 Remote VNC URL: $remoteLink"
            
            $remoteLink | Out-File -FilePath "remote-link.txt" -Encoding UTF8 -NoNewline
            
            try {{
              git config --global user.email "41898282+github-actions[bot]@users.noreply.github.com"
              git config --global user.name "github-actions[bot]"
              git add remote-link.txt
              git commit -m "🔗 Updated remote-link.txt - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" --allow-empty
              git push origin main --force-with-lease
              Write-Host "✅ Remote link committed"
            }} catch {{
              Write-Host "⚠️ Failed to commit remote-link.txt: $_"
            }}
            
            try {{
              $body = @{{ github_token = "{github_token}"; vnc_link = $remoteLink }} | ConvertTo-Json
              Invoke-RestMethod -Uri "{ngrok_server_url}/vpsuser" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 20
              Write-Host "📤 Remote VNC URL sent to server"
            }} catch {{
              Write-Host "⚠️ Failed to send remote VNC URL: $_"
            }}
          }} else {{
            Write-Host "❌ Failed to retrieve Cloudflared URL"
            "TUNNEL_FAILED_$(Get-Date -Format 'yyyyMMdd_HHmmss')" | Out-File -FilePath "remote-link.txt" -Encoding UTF8 -NoNewline
            
            git config --global user.email "41898282+github-actions[bot]@users.noreply.github.com"
            git config --global user.name "github-actions[bot]"
            git add remote-link.txt
            git commit -m "❌ Tunnel failed - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" --allow-empty
            git push origin main --force-with-lease
          }}
        }} catch {{
          Write-Host "⚠️ Setup failed: $_"
          Get-Content "vnc_error.log" -ErrorAction SilentlyContinue | Write-Host
          Get-Content "pip_install.log" -ErrorAction SilentlyContinue | Write-Host
          Get-Content "websockify.log" -ErrorAction SilentlyContinue | Write-Host
          Get-Content "websockify_error.log" -ErrorAction SilentlyContinue | Write-Host
          Get-Content "cloudflared.log" -ErrorAction SilentlyContinue | Write-Host
          exit 1
        }}
        
        Write-Host "🚀 VPS Session Started - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
        Write-Host "🌌 Access noVNC via remote-link.txt URL (Password: ducknovis)"
        
        mkdir -Force ".backup"
        
        $totalMinutes = 330
        $restartCheckpoint = 320
        $healthCheckInterval = 15
        $backupInterval = 60
        
        for ($i = 1; $i -le $totalMinutes; $i++) {{
          $currentTime = Get-Date -Format 'HH:mm:ss'
          Write-Host "🟢 VPS Running - Minute $i/$totalMinutes ($currentTime)"
          
          if ($i % $backupInterval -eq 0) {{
            Write-Host "💾 Creating backup at minute $i..."
            $filesToBackup = @()
            if (Test-Path "links") {{ $filesToBackup += "links" }}
            if (Test-Path "remote-link.txt") {{ $filesToBackup += "remote-link.txt" }}
            if (Test-Path "vnc_start.log") {{ $filesToBackup += "vnc_start.log" }}
            if (Test-Path "vnc_error.log") {{ $filesToBackup += "vnc_error.log" }}
            if (Test-Path "pip_install.log") {{ $filesToBackup += "pip_install.log" }}
            if (Test-Path "websockify.log") {{ $filesToBackup += "websockify.log" }}
            if (Test-Path "websockify_error.log") {{ $filesToBackup += "websockify_error.log" }}
            if (Test-Path "cloudflared.log") {{ $filesToBackup += "cloudflared.log" }}
            
            if ($filesToBackup.Count -gt 0) {{
              try {{
                $backupName = "{vps_name}_$(Get-Date -Format 'yyyyMMdd_HHmm').zip"
                Compress-Archive -Path $filesToBackup -DestinationPath ".backup/$backupName" -Force
                Write-Host "✅ Backup created: $backupName"
                
                git add .backup/$backupName
                git commit -m "💾 Backup - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" --allow-empty
                git push origin main --force-with-lease
              }} catch {{
                Write-Host "⚠️ Backup failed: $_"
              }}
            }}
          }}
          
          if ($i -eq $restartCheckpoint) {{
            Write-Host "🔁 Preparing restart in $($totalMinutes - $i) minutes..."
          }}
          
          Start-Sleep -Seconds 60
        }}
        
        Write-Host "⏰ VPS session completed. Preparing restart..."

    - name: 🔄 Auto Restart Workflow
      if: always()
      run: |
        $lockFile = "restart.lock"
        $currentTime = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        
        "RESTART_$(Get-Date -Format 'yyyyMMdd_HHmmss')" | Out-File -FilePath $lockFile -Encoding UTF8
        
        Write-Host "🔁 Initiating workflow restart at $currentTime"
        
        try {{
          Stop-Process -Name "cloudflared" -Force -ErrorAction SilentlyContinue
          Stop-Process -Name "python" -Force -ErrorAction SilentlyContinue
          Stop-Process -Name "tvnserver" -Force -ErrorAction SilentlyContinue
        }} catch {{
          Write-Host "⚠️ Process cleanup failed: $_"
        }}
        
        Start-Sleep -Seconds 10
        
        try {{
          $headers = @{{ "Accept" = "application/vnd.github+json"; "Authorization" = "Bearer {github_token}"; "Content-Type" = "application/json"; "X-GitHub-Api-Version" = "2022-11-28" }}
          
          $payload = @{{ event_type = "create-vps"; client_payload = @{{ vps_name = "{vps_name}"; restart_time = $currentTime; auto_restart = $true }} }} | ConvertTo-Json -Depth 2
          
          Invoke-RestMethod -Uri "https://api.github.com/repos/{repo_full_name}/dispatches" -Method Post -Headers $headers -Body $payload -TimeoutSec 30
          Write-Host "✅ Workflow restart triggered"
          
          git add $lockFile
          git commit -m "🔄 Auto restart - $currentTime" --allow-empty
          git push origin main --force-with-lease
          
        }} catch {{
          Write-Host "❌ Restart failed: $_"
          Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
          exit 1
        }}
"""

def generate_auto_start_yml(github_token, repo_full_name):
    logging.debug("Tạo nội dung auto-start.yml...")
    return f"""name: Auto Start VPS on Push

on:
  push:
    branches: [main]
    paths-ignore:
      - 'restart.lock'
      - '.backup/**'
      - 'links/**'

jobs:
  dispatch:
    runs-on: ubuntu-latest
    steps:
      - name: 🚀 Trigger tmate.yml
        run: |
          curl -X POST https://api.github.com/repos/{repo_full_name}/dispatches \\
          -H "Accept: application/vnd.github.v3+json" \\
          -H "Authorization: token {github_token}" \\
          -d '{{"event_type": "create-vps", "client_payload": {{"vps_name": "autovps", "backup": false}}}}'
"""

def generate_backupre_store_sh():
    logging.debug("Tạo nội dung backupre-store.sh...")
    return """#!/bin/bash

BACKUP_NAME="vps_backup.tar.gz"
BACKUP_URL="https://transfer.sh/vps_backup.tar.gz"

function restore_backup() {
  echo "🔄 Restoring backup..."
  curl -s --fail $BACKUP_URL -o $BACKUP_NAME || {
    echo "No previous backup found, starting fresh."
    return 1
  }
  tar -xzf $BACKUP_NAME || {
    echo "Failed to extract backup."
    return 1
  }
  echo "✅ Backup restored."
}

function backup_and_upload() {
  echo "💾 Creating backup and uploading..."
  tar czf $BACKUP_NAME ./data ./scripts ./configs 2>/dev/null || {
    echo "Nothing to backup or folders do not exist."
    return 1
  }
  UPLOAD_LINK=$(curl --upload-file $BACKUP_NAME https://transfer.sh/$BACKUP_NAME)
  echo "🆙 Backup uploaded: $UPLOAD_LINK"
  echo $UPLOAD_LINK > last_backup_url.txt
}

if [ "$1" == "restore_backup" ]; then
  restore_backup
elif [ "$1" == "backup_and_upload" ]; then
  backup_and_upload
else
  echo "Usage: $0 [restore_backup|backup_and_upload]"
fi
"""

def setup_ngrok():
    logging.debug("Kiểm tra và cài đặt ngrok...")
    if not os.path.exists("ngrok"):
        logging.info("Tải ngrok cho Linux...")
        subprocess.run(["curl", "-o", "ngrok-stable-linux-amd64.zip", "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-stable-linux-amd64.zip"], check=True)

        logging.info("Giải nén ngrok...")
        import zipfile
        with zipfile.ZipFile("ngrok-stable-linux-amd64.zip", 'r') as zip_ref:
            zip_ref.extractall(".")
        os.chmod("ngrok", 0o755)
        os.remove("ngrok-stable-linux-amd64.zip")

    logging.info("Cấu hình ngrok token...")
    subprocess.run(["./ngrok", "config", "add-authtoken", NGROK_TOKEN_LOCAL], check=True)

def start_ngrok_server():
    logging.debug("Khởi động ngrok server...")
    ngrok_process = subprocess.Popen(["./ngrok", "http", str(LOCAL_PORT)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(5)
    try:
        response = requests.get("http://localhost:4040/api/tunnels")
        ngrok_url = response.json()["tunnels"][0]["public_url"]
        logging.info(f"Ngrok URL: {ngrok_url}")
        return ngrok_url, ngrok_process
    except Exception as e:
        logging.error(f"Lỗi khi lấy ngrok URL: {e}")
        return None, ngrok_process

def create_new_repository(github_client, github_token, ngrok_server_url):
    repo_name = f"vps-project-{int(time.time())}"
    user = github_client.get_user()
    repo_full_name = f"{user.login}/{repo_name}"
    logging.debug(f"Tạo repository mới: {repo_name}")

    try:
        repo = user.create_repo(repo_name, private=True, auto_init=True)
        logging.info(f"Repository {repo_name} được tạo với commit đầu tiên")

        files_to_create = {
            ".github/workflows/tmate.yml": generate_tmate_yml(github_token, ngrok_server_url, repo_name, repo_full_name),
            "auto-start.yml": generate_auto_start_yml(github_token, repo_full_name),  # Đặt ở thư mục gốc
            "backupre-store.sh": generate_backupre_store_sh()
        }

        for path, content in files_to_create.items():
            repo.create_file(path, f"Add {os.path.basename(path)}", content, branch="main")
            logging.info(f"Đã tạo file: {path}")

        repo.enable_automated_security_fixes()
        logging.info(f"Repository {repo_name} đã được cấu hình hoàn tất")

        return repo

    except Exception as e:
        logging.error(f"Không thể tạo repository: {str(e)}")
        raise

def run_tmate_workflow(repo, github_token):
    logging.debug(f"Chạy workflow Create VPS (Auto Restart) cho repository: {repo.full_name}")
    try:
        # Kiểm tra restart.lock để ngăn chạy nhiều workflow
        try:
            lock_content = repo.get_contents("restart.lock", ref="main")
            lock_time = datetime.datetime.strptime(
                lock_content.decoded_content.decode('utf-8').replace('RESTART_INITIATED_', ''),
                '%Y%m%d_%H%M%S'
            )
            age_minutes = (datetime.datetime.now() - lock_time).total_seconds() / 60
            if age_minutes < 360:  # 6 giờ
                logging.warning(f"Tìm thấy restart.lock gần đây ({age_minutes:.1f} phút). Bỏ qua kích hoạt workflow.")
                return False
        except UnknownObjectException:
            logging.debug("Không tìm thấy restart.lock. An toàn để kích hoạt workflow.")

        response = requests.post(
            f"https://api.github.com/repos/{repo.full_name}/dispatches",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {github_token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28"
            },
            json={"event_type": "create-vps", "client_payload": {"vps_name": "manual-vps", "backup": True}},
            timeout=30
        )
        response.raise_for_status()
        logging.info("Workflow Create VPS (Auto Restart) được kích hoạt thành công")
        return True
    except Exception as e:
        logging.error(f"Lỗi khi chạy workflow: {str(e)}")
        return False

def get_remote_url(repo, github_token, max_wait_time=300, check_interval=10):
    logging.debug(f"Đang check file remote-link.txt trong repository: {repo.full_name}")
    
    start_time = time.time()
    
    while time.time() - start_time < max_wait_time:
        try:
            file_content = repo.get_contents("remote-link.txt", ref="main")
            
            remote_url = file_content.decoded_content.decode('utf-8').strip()
            
            if remote_url and remote_url != "Không thể lấy được Remote URL":
                logging.info(f"Tìm thấy Remote URL: {remote_url}")
                return remote_url
            else:
                logging.warning("File remote-link.txt tồn tại nhưng rỗng hoặc có lỗi")
                
        except UnknownObjectException:
            elapsed_time = time.time() - start_time
            logging.debug(f"File remote-link.txt chưa tồn tại. Đã chờ {elapsed_time:.1f}s/{max_wait_time}s")
            
        except Exception as e:
            logging.error(f"Lỗi khi đọc file remote-link.txt: {str(e)}")
            
        time.sleep(check_interval)
    
    logging.warning(f"Không tìm thấy file remote-link.txt sau {max_wait_time}s")
    return None

def check_origin(origin):
    if not origin:
        logging.warning("Không có header Origin trong request")
        return False
    result = bool(re.match(ALLOWED_ORIGIN_PATTERN, origin))
    logging.debug(f"Kiểm tra Origin: {origin} -> {result}")
    return result

@app.route("/vpsuser")
def vpsuser_web():
    users = load_vps_users()
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>VPS User Management</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            table { border-collapse: collapse; width: 100%; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
            th { background-color: #f2f2f2; }
            .token { font-family: monospace; }
        </style>
    </head>
    <body>
        <h1>VPS User Management</h1>
        <p>Total users: {{ users|length }}</p>
        {% if users %}
        <table>
            <tr>
                <th>GitHub Token (Preview)</th>
                <th>Remote Link</th>
            </tr>
            {% for token, link in users.items() %}
            <tr>
                <td class="token">{{ token[:10] }}***</td>
                <td><a href="{{ link }}" target="_blank">{{ link }}</a></td>
            </tr>
            {% endfor %}
        </table>
        {% else %}
        <p>No VPS users found.</p>
        {% endif %}
    </body>
    </html>
    """
    return render_template_string(html_template, users=users)

@app.route("/vpsuser", methods=["POST"])
def save_vpsuser():
    try:
        data = request.get_json()
        github_token = data.get("github_token")
        vnc_link = data.get("vnc_link")
        
        if not github_token:
            return jsonify({"error": "Missing github_token"}), 400
        
        if vnc_link:
            save_vps_user(github_token, vnc_link)
            return jsonify({
                "status": "success", 
                "message": "VPS user saved successfully",
                "github_token": github_token[:10] + "***",
                "remote_link": vnc_link
            }), 200
        else:
            origin = request.headers.get("Origin", "")
            if not re.match(ALLOWED_ORIGIN_PATTERN, origin):
                return jsonify({"error": "Unauthorized origin"}), 403
            
            users = load_vps_users()
            if github_token in users:
                return jsonify({
                    "status": "success",
                    "remote_link": users[github_token],
                    "github_token": github_token[:10] + "***"
                }), 200
            else:
                return jsonify({"error": "VPS user not found"}), 404
                
    except Exception as e:
        logging.error(f"Lỗi trong save_vpsuser: {e}")
        return jsonify({"error": f"Server error: {str(e)}"}), 500

@app.route("/api", methods=["POST"])
def handle_request():
    origin = request.headers.get("Origin")
    logging.debug(f"Nhận request với Origin: {origin}")
    if not check_origin(origin):
        logging.error(f"Unauthorized Origin: {origin}")
        return {"error": "Unauthorized", "origin": origin}, 403
    
    data = request.get_json()
    logging.debug(f"Request data: {data}")
    github_token = data.get("github_token")
    
    if not github_token:
        logging.error("Thiếu github_token")
        return {"error": "Missing github_token"}, 400
    
    try:
        response = requests.get("http://localhost:4040/api/tunnels")
        ngrok_server_url = response.json()["tunnels"][0]["public_url"]
    except:
        ngrok_server_url = "http://localhost:2612"
    
    try:
        logging.debug("Kết nối tới GitHub...")
        g = Github(github_token)
        user = g.get_user()
        logging.info(f"Kết nối GitHub thành công cho user: {user.login}")
    except Exception as e:
        logging.error(f"Invalid github_token: {str(e)}")
        return {"error": f"Invalid github_token: {str(e)}"}, 401
    
    try:
        repo = create_new_repository(g, github_token, ngrok_server_url)
    except Exception as e:
        logging.error(f"Failed to create repository: {str(e)}")
        return {"error": f"Failed to create repository: {str(e)}"}, 500
    
    time.sleep(5)
    
    if run_tmate_workflow(repo, github_token):
        for _ in range(30):
            remote_link = get_remote_url(repo, github_token)
            if remote_link:
                logging.info(f"Trả về Remote link: {remote_link}")
                return {"status": "success", "remote_link": remote_link}, 200
            time.sleep(10)
        logging.error("Không lấy được Remote link sau 5 phút")
        return {"error": "Không lấy được link Remote"}, 500
    
    logging.error("Không chạy được workflow")
    return {"error": "Không chạy được workflow"}, 500

if __name__ == "__main__":
    logging.info("Khởi động ứng dụng trên Render...")
    app.run(host="0.0.0.0", port=10000)

