# NetScope Dashboard

This folder contains the secured NetScope web dashboard.

## Main pages

- **Overview** — live traffic, network health, device status, open alerts and a small network map.
- **Devices** — registered devices, their current state and health.
- **Access Points** — wireless devices currently being monitored.
- **New Devices** — review and approve devices that connect for the first time.
- **Alerts** — issues that are still happening.
- **Network Map** — device connections and affected parts of the network.
- **Traffic** — recent network activity and the latest check scores.
- **Checks** — issue trends, traffic trends and devices with the most alerts.
- **History** — current and past alerts.
- **Troubleshooting** — likely causes, supporting information and suggested actions.
- **Settings** — add, edit or remove monitored devices without editing `config.yaml` directly.

The manual device-message test was removed from the Troubleshooting page. Real device messages continue to be collected by the backend collector.

## Run locally

Start the backend from the project root:

```powershell
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Then start the dashboard:

```powershell
cd dashboard\frontend
npm install
npm run dev
```

Open `http://localhost:5173` and sign in.

## Production build

```powershell
npm run build
```

The build output is written to `dist/`.
