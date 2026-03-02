# Option 2: Move Ubuntu Docker Storage to D: Drive

We will reconfigure the local Docker Engine running *inside* your Ubuntu environment to save all its images and container data to the D: drive instead of using the C: drive space.

## Step-by-Step Instructions

1. **Create the target Docker folder on your D: drive within Linux**
   Open your Ubuntu terminal and run this command:
   ```bash
   sudo mkdir -p /mnt/d/DockerStorageUbuntu
   ```

2. **Stop the Docker service**
   We need to shut down the Docker daemon before moving any files:
   ```bash
   sudo service docker stop
   ```

3. **Create the Docker configuration file**
   We need to tell Docker where to put the new files. We do this by creating a `daemon.json` file.
   Run this command in the Ubuntu terminal to create it:
   ```bash
   echo '{"data-root": "/mnt/d/DockerStorageUbuntu"}' | sudo tee /etc/docker/daemon.json
   ```

4. **Copy your existing Docker images to the new location (Optional but recommended)**
   If you want to keep the massive LLM images you've already downloaded, copy them over:
   ```bash
   sudo rsync -aP /var/lib/docker/ /mnt/d/DockerStorageUbuntu/
   ```
   *(If you don't care about keeping the old images and just want to download them later, you can skip this step).*

5. **Start Docker again**
   ```bash
   sudo service docker start
   ```

6. **Verify the change worked**
   Check where Docker is now saving its files by running:
   ```bash
   docker info | grep 'Docker Root Dir'
   ```
   It should output `Docker Root Dir: /mnt/d/DockerStorageUbuntu`.

7. **Free up your C: drive space**
   Once you've verified docker is running correctly and pointing to `/mnt/d/DockerStorageUbuntu`, you can safely delete the old docker folder to instantly reclaim gigabytes of space on your C: drive:
   ```bash
   sudo rm -rf /var/lib/docker
   ```

---
*Note: Because the /mnt/d partition represents a translation to the Windows NTFS filesystem, some highly specific Docker container permissions setups might occasionally have warnings, but for simply running standard model-server containers, this configuration will work perfectly and permanently redirect disk usage to the D: drive.*
