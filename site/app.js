const download = document.querySelector(".download");
const downloadMeta = document.querySelector("#downloadMeta");

fetch("/JARVIS.exe", { method: "HEAD", cache: "no-store" })
  .then((response) => {
    const bytes = Number(response.headers.get("content-length"));
    if (response.ok && Number.isFinite(bytes) && bytes > 0) {
      downloadMeta.textContent = `JARVIS.exe · ${(bytes / 1024 / 1024).toFixed(1)} MB`;
    } else {
      downloadMeta.textContent = "JARVIS.exe · Windows x64";
    }
  })
  .catch(() => {
    downloadMeta.textContent = "JARVIS.exe · Windows x64";
  });

download?.addEventListener("click", () => {
  download.querySelector("span").textContent = "Download starting…";
  window.setTimeout(() => {
    download.querySelector("span").textContent = "Download for Windows";
  }, 2500);
});
