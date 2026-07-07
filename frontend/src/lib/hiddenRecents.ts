import { useState, useCallback, useEffect } from "react";

/**
 * LocalStorage-backed "hidden recent searches" — purely a UI dismiss list.
 * The underlying scraped data (DiscoveryResult / SocialPost) is NOT touched,
 * so future searches still reuse the cache and avoid re-scraping costs.
 */
function readSet(key: string): Set<string> {
  try {
    const raw = localStorage.getItem(key);
    return new Set<string>(raw ? JSON.parse(raw) : []);
  } catch {
    return new Set();
  }
}

function writeSet(key: string, s: Set<string>) {
  try {
    localStorage.setItem(key, JSON.stringify([...s]));
  } catch {
    /* quota / private mode — ignore */
  }
}

export function useHiddenRecents(storageKey: string) {
  const [hidden, setHidden] = useState<Set<string>>(() => readSet(storageKey));

  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key === storageKey) setHidden(readSet(storageKey));
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [storageKey]);

  const hide = useCallback((q: string) => {
    setHidden(prev => {
      const next = new Set(prev);
      next.add(q);
      writeSet(storageKey, next);
      return next;
    });
  }, [storageKey]);

  const unhide = useCallback((q: string) => {
    setHidden(prev => {
      const next = new Set(prev);
      next.delete(q);
      writeSet(storageKey, next);
      return next;
    });
  }, [storageKey]);

  const isHidden = useCallback((q: string) => hidden.has(q), [hidden]);

  return { hide, unhide, isHidden, hidden };
}
