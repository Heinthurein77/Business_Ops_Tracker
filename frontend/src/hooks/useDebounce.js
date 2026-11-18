import { useEffect, useRef } from 'react';

/** Debounced effect (F-m2): runs `fn` only after `value` stops changing
 * for `delay` ms. Draft autosave previously fired a synchronous
 * `localStorage.setItem` + `JSON.stringify` on every keystroke. */
export function useDebouncedEffect(fn, value, delay = 400) {
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    const timer = setTimeout(() => fnRef.current(), delay);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, delay]);
}
