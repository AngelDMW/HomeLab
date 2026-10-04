# 🌩️ HomeLab: OptiPlex 5040

![Debian](https://img.shields.io/badge/Debian-12-A81D33?style=flat-square&logo=debian)
![Docker](https://img.shields.io/badge/Docker-24.0-2496ED?style=flat-square&logo=docker)
![Tailscale](https://img.shields.io/badge/Tailscale-Mesh-white?style=flat-square&logo=tailscale)

My personal self-hosted infrastructure. Running on a Dell OptiPlex 5040 out of Santiago, DR. This lab handles my live web portfolio, a private cloud environment, and local network services—all running 24/7 without exposing local ports to the open internet.

## 🏗️ Hardware Specs
* **Host:** Dell OptiPlex 5040
* **OS:** Debian 12 (Bookworm)
* **Network:** Bridged routers (Wind ZTE LAN -> Huawei Claro AP)
* **Connected Clients:** MacBook, iPhone 12 Mini, Nintendo Switch (Kubuntu ARM64)

## ⚙️ The Stack
I rely heavily on Docker for containerization and Tailscale for zero-trust networking. 

* **Containers:** Managed via Docker Compose and Portainer.
* **Reverse Proxy:** Nginx routing internal traffic.
* **Security:** No port-forwarding on the physical ISP router. Public exposure (like my dev portfolio) is handled securely via **Tailscale Funnel** utilizing SSL/HTTPS tunnels.

## 🚀 Deployed Services

| Service | Purpose | Exposure |
| :--- | :--- | :--- |
| **[MrDavid.dev](#)** | Live interactive dev portfolio | Public (Tailscale Funnel) |
| **Nginx** | Reverse proxy handling web traffic | Port 8081 |
| **Nextcloud** | Private cloud managing 66GB+ of personal media | Tailnet (Private) |
| **NC Recognize** | Local ML (TensorFlow) for photo tagging | Internal |
| **Portainer** | Container management UI | Internal |

## 📂 Repository Structure
* `/docker` - Base Compose files for all running services.
* `/web-portfolio` - Source code for my live SPA portfolio.
* `/docs` - Architecture diagrams and setup documentation.

---
**Angel Diaz** — *Full-Stack & Mobile Engineer*  
[GitHub](https://github.com/AngelDMW) • [LinkedIn](https://www.linkedin.com/in/angel-diaz-911a39238)
