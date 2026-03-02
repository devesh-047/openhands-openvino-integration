# Move Docker Storage to D Drive

This guide explains how to move Docker Desktop's WSL storage (which contains all your Docker images, containers, and volumes) from your `C:` drive to `D:\DockerStorage`.

## Step-by-Step Instructions

1. **Quit Docker Desktop**
   Make sure Docker Desktop is completely closed (right-click its icon in the system tray and select "Quit Docker Desktop").

2. **Open PowerShell**
   Open PowerShell on your Windows host.

3. **Shut down WSL**
   Stop any running WSL distributions to ensure no files are locked:
   ```powershell
   wsl --shutdown
   ```

4. **Export the `docker-desktop-data` distribution**
   This distribution holds your Docker images. We will export it to a temporary `.tar` file on your D drive:
   ```powershell
   wsl --export docker-desktop-data "D:\docker-desktop-data.tar"
   ```
   *(Note: This might take a few minutes depending on how many Docker images you have).*

5. **Unregister the old distribution**
   This removes the storage from your C drive:
   ```powershell
   wsl --unregister docker-desktop-data
   ```

6. **Create the target folder on your D drive (if it doesn't exist)**
   ```powershell
   mkdir "D:\DockerStorage"
   ```

7. **Import the distribution to the new location**
   This restores your Docker data into the new folder on the D drive:
   ```powershell
   wsl --import docker-desktop-data "D:\DockerStorage" "D:\docker-desktop-data.tar" --version 2
   ```

8. **Restart Docker Desktop**
   Open Docker Desktop again. It will now use `D:\DockerStorage` for all its image and container storage.

9. **Clean up**
   Once you've confirmed Docker is working correctly and your images are present, you can safely delete the temporary `.tar` file:
   ```powershell
   Remove-Item "D:\docker-desktop-data.tar"
   ```
