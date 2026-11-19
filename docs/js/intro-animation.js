"use strict";

(() => {
  const splash = document.getElementById("intro-splash");
  const video = document.getElementById("intro-video");
  const skipButton = document.getElementById("intro-skip");
  if (!splash || !video || !skipButton) return;

  let finished = false;
  let fallbackTimer;
  const finish = () => {
    if (finished) return;
    finished = true;
    window.clearTimeout(fallbackTimer);
    video.pause();
    splash.classList.add("is-leaving");
    window.setTimeout(() => { splash.hidden = true; }, 450);
  };

  skipButton.addEventListener("click", finish);
  video.addEventListener("ended", finish, { once: true });
  video.addEventListener("error", finish, { once: true });

  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    finish();
    return;
  }

  splash.hidden = false;
  fallbackTimer = window.setTimeout(finish, 7500);
  try {
    Promise.resolve(video.play()).catch(finish);
  } catch (error) {
    finish();
  }
})();
