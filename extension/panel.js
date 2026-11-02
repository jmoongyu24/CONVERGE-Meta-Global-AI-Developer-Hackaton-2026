// Shows Converge beside the open tab. When the tab is a Notion page, Converge opens that
// team's board, or offers to connect the page if it is new.
const CONVERGE = "http://localhost:8000";
const frame = document.getElementById("converge");
let shown = null;

const isNotion = (url) => /^https:\/\/([\w-]+\.)?notion\.(so|site)\//.test(url);

async function follow() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  const page = (tab?.url ?? "").split(/[?#]/)[0];
  // Away from Notion, keep showing the last Notion page's team.
  const target = isNotion(page)
    ? `${CONVERGE}/?panel=1&notion=${encodeURIComponent(page)}`
    : (shown ?? `${CONVERGE}/?panel=1`);
  if (target === shown) return;
  shown = target;
  frame.src = target;
}

fetch(`${CONVERGE}/api/health`).then(
  () => {
    follow();
    chrome.tabs.onActivated.addListener(follow);
    chrome.tabs.onUpdated.addListener((_, change) => change.url && follow());
  },
  () => {
    document.getElementById("offline").style.display = "block";
    frame.style.display = "none";
  },
);
