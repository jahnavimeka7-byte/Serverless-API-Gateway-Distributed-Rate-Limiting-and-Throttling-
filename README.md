# Serverless API Gateway with Distributed Rate-Limiting and Throttling

A responsive front-end demonstration for a Cloud Computing project. It simulates API requests, a five-request-per-minute limit, allowed requests (HTTP 200), and throttled requests (HTTP 429).

## Features
- API request simulator
- Live session counters for total, allowed, and throttled requests
- 60-second rate-limit window in the browser demo
- Recent request activity table
- Responsive dashboard layout
- Reset demo button

## Run locally
1. Download or clone this repository.
2. Open `index.html` in a browser.
3. Click **Send API Request** several times to see throttling.

## Publish with GitHub Pages
1. Sign in to GitHub as `jahnavimeka7-byte`.
2. Create a new public repository named `serverless-api-gateway`.
3. Upload `index.html`, `style.css`, `script.js`, and `README.md` to the repository root.
4. Open **Settings → Pages**.
5. Under **Build and deployment**, choose **Deploy from a branch**.
6. Select branch **main** and folder **/(root)**, then click **Save**.
7. Wait for the deployment to finish. Your website URL should be:
   `https://jahnavimeka7-byte.github.io/serverless-api-gateway/`

## Important project note
This repository is a browser-based simulator, not a production serverless API. The rate counter exists in one browser session and is not shared across users or servers. A real distributed implementation needs server-side enforcement, an API Gateway, a serverless function, and shared storage (for example, Redis or a managed database) using atomic counter updates and expiration.

## Suggested production architecture
Client → API Gateway → Serverless Function → Shared rate-limit store
