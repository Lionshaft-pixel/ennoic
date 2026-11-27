"use strict";

// Bump this version whenever any of these public offline resources changes.
const CACHE_PREFIX = "ennoic-public-offline-";
const CACHE_NAME = `${CACHE_PREFIX}v1`;
const OFFLINE_URL = "/offline";
const PUBLIC_ASSETS = [
  OFFLINE_URL,
  "/static/pwa/offline.css",
  "/static/pwa/offline.js",
  "/static/email/ennoic-logo.png",
];
const OFFLINE_PAGES = new Set(["/", "/app", "/settings", "/pricing", "/terms", "/privacy", "/refund-policy"]);

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    for (const path of PUBLIC_ASSETS) {
      // No cookies, redirects, or personalized responses enter the offline cache.
      const response = await fetch(new Request(path, {
        credentials: "omit", cache: "reload", redirect: "error",
      }));
      if (!response.ok || response.type === "opaque" || response.redirected) {
        throw new Error("Public offline resource unavailable");
      }
      await cache.put(path, response);
    }
    // Safe to activate immediately: application HTML/CSS/JS are never cached here.
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((key) => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME)
      .map((key) => caches.delete(key)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || url.search
      || request.headers.has("Authorization") || request.headers.has("Range")) return;

  if (PUBLIC_ASSETS.includes(url.pathname)) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE_NAME);
      return (await cache.match(url.pathname)) || fetch(request);
    })());
    return;
  }
  if (request.mode === "navigate" && OFFLINE_PAGES.has(url.pathname)) {
    // Never store a navigation response, including errors and login redirects.
    event.respondWith(fetch(request).catch(async () => {
      const cache = await caches.open(CACHE_NAME);
      return (await cache.match(OFFLINE_URL)) || new Response("Ennoic is offline. Reconnect and retry.", {
        status: 503, headers: {"Content-Type": "text/plain; charset=utf-8"},
      });
    }));
  }
  // Everything else is untouched: auth, API, chat, tasks, payments, third parties,
  // and even the app's own JS/CSS. No runtime caching or background mutations.
});
