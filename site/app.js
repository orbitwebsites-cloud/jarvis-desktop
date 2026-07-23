const download = document.querySelector(".download");

download?.addEventListener("click", () => {
  download.querySelector("span").textContent = "Download starting…";
  window.setTimeout(() => {
    download.querySelector("span").textContent = "Download for Windows";
  }, 2500);
});
