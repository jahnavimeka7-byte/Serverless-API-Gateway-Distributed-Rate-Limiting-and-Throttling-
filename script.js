const LIMIT = 5;
const WINDOW_MS = 60 * 1000;
let requestTimes = [];
let total = 0, allowed = 0, blocked = 0, nextId = 1;
const $ = (id) => document.getElementById(id);

function updateDashboard() {
  $("total").textContent = total;
  $("allowed").textContent = allowed;
  $("blocked").textContent = blocked;
  const now = Date.now();
  requestTimes = requestTimes.filter(t => now - t < WINDOW_MS);
  $("usageLabel").textContent = `${requestTimes.length} of ${LIMIT} requests`;
  $("progressBar").style.width = `${Math.min(100, requestTimes.length / LIMIT * 100)}%`;
}

function sendRequest() {
  const now = Date.now();
  requestTimes = requestTimes.filter(t => now - t < WINDOW_MS);
  total++;
  const id = nextId++;
  const accepted = requestTimes.length < LIMIT;
  if (accepted) {
    requestTimes.push(now);
    allowed++;
  } else {
    blocked++;
  }
  const time = new Date(now).toLocaleTimeString();
  const row = document.createElement("tr");
  const values = [`REQ-${String(id).padStart(4, "0")}`, time, "/api/demo", accepted ? "200 OK" : "429 Too Many Requests", accepted ? "Allowed" : "Throttled"];
  values.forEach((value, index) => {
    const cell = document.createElement("td");
    if (index === 3) {
      const pill = document.createElement("span");
      pill.className = `pill ${accepted ? "ok" : "blocked"}`;
      pill.textContent = value;
      cell.appendChild(pill);
    } else {
      cell.textContent = value;
    }
    row.appendChild(cell);
  });
  const body = $("activity");
  if (body.querySelector(".empty")) body.innerHTML = "";
  body.prepend(row);
  while (body.children.length > 10) body.removeChild(body.lastChild);

  const notice = $("notice");
  notice.className = `notice ${accepted ? "success" : "error"}`;
  notice.textContent = accepted
    ? `Request REQ-${String(id).padStart(4, "0")} accepted. HTTP 200 OK — request is within the current limit.`
    : `Request REQ-${String(id).padStart(4, "0")} throttled. HTTP 429 Too Many Requests — wait for the 60-second window to refresh.`;
  updateDashboard();
}

function resetDemo() {
  requestTimes = [];
  total = 0; allowed = 0; blocked = 0; nextId = 1;
  $("activity").innerHTML = '<tr class="empty"><td colspan="5">No requests yet. Click “Send API Request”.</td></tr>';
  $("notice").className = "notice neutral";
  $("notice").textContent = "Demo reset. Send a request to begin the simulation.";
  updateDashboard();
}
$("sendBtn").addEventListener("click", sendRequest);
$("resetBtn").addEventListener("click", resetDemo);
updateDashboard();
