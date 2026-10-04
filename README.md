# 🖥️ MrDavid's Home Lab Infrastructure

![Debian](https://img.shields.io/badge/Debian-12-A81D33?style=for-the-badge&logo=debian)
![Docker](https://img.shields.io/badge/Docker-24.0-2496ED?style=for-the-badge&logo=docker)
![Tailscale](https://img.shields.io/badge/Tailscale-VPN-white?style=for-the-badge&logo=tailscale)
![Nginx](https://img.shields.io/badge/Nginx-Web_Server-009639?style=for-the-badge&logo=nginx)

Bienvenido al repositorio de mi infraestructura autoalojada (Self-Hosted Home Lab). Este proyecto documenta el servidor físico que administro desde Santiago, República Dominicana, el cual aloja mi portafolio web interactivo, nube privada y servicios de red locales operando 24/7.

## 🧠 Arquitectura y Filosofía
El objetivo de este servidor es mantener un control absoluto sobre mis datos y despliegues sin depender de servicios en la nube, manteniendo un consumo energético eficiente y una seguridad robusta sin necesidad de abrir puertos en el router local.

### ⚙️ Hardware (El Servidor)
- **Máquina:** Dell OptiPlex 5040
- **Red Local:** Configuración de routers en puente (Wind ZTE LAN -> Huawei Claro AP)
- **Dispositivos Conectados:** MacBook, iPhone 12 Mini, Nintendo Switch (Kubuntu ARM64)

### 🧰 Software Stack
- **OS:** Debian GNU/Linux 12 (Bookworm)
- **Contenedores:** Docker + Portainer (Gestión de UI)
- **Networking:** Tailscale (Zero-Trust VPN) + Tailscale Funnel (Túneles públicos HTTPS)

---

## 🚀 Servicios Desplegados

| Servicio | Descripción | Exposición |
|----------|-------------|------------|
| **[MrDavid.dev](#)** | Portafolio personal Full-Stack interactivo servido nativamente. | Pública (Tailscale Funnel) |
| **Nginx** | Servidor web proxy inverso de alto rendimiento. | Puerto 8081 |
| **Nextcloud** | Nube privada para gestión de archivos fotográficos y galerías. | Privada (Tailnet) |
| **Portainer** | Panel de control visual para la gestión de contenedores Docker. | Privada |

---

## 🛡️ Seguridad y Redes (Zero-Trust)
Una de las características principales de este Home Lab es su seguridad. **No hay puertos abiertos hacia internet en el router físico**. Toda la exposición pública (como el portafolio) se realiza a través de **Tailscale Funnel**, el cual crea un túnel seguro cifrado mediante SSL.

## 👨‍💻 Autor
**Angel Diaz** - *Full-Stack & Mobile Developer*
- [GitHub](https://github.com/AngelDMW)
- [LinkedIn](https://www.linkedin.com/in/angel-diaz-911a39238)
