import '@testing-library/jest-dom'

// lightweight-charts / fancy-canvas needs matchMedia and ResizeObserver in jsdom
if (typeof window !== 'undefined' && !window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    value: () => ({
      matches: false,
      media: '',
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
    writable: true,
  })
}
if (typeof globalThis !== 'undefined' && typeof (globalThis as any).ResizeObserver === 'undefined') {
  ;(globalThis as any).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}
if (typeof window !== 'undefined' && typeof (window as any).ResizeObserver === 'undefined') {
  ;(window as any).ResizeObserver = (globalThis as any).ResizeObserver
}
