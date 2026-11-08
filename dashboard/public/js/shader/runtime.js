// Vanilla-JS port of Figma's shader runtime (ShaderFill + useShaderRuntime + webgpu.ts), for this
// framework-free app. Behaviour kept: one shared WebGPU device, per-canvas context + input texture,
// effect setup() once, render() per frame while the manifest says isAnimated, device-pixel resize.
// As in ShaderFill, a canvas whose runtime fails (no WebGPU, no adapter) is removed: the element under it shows.
// Additions for a page with many cards: the clock pauses while the canvas is off-screen, honours
// prefers-reduced-motion (one frame), and tears down when the canvas leaves the DOM.

let devicePromise = null;

/** Module-level singleton: one GPUDevice backs every shader fill on the page (webgpu.ts). */
function acquireDevice() {
  if (!devicePromise) {
    devicePromise = (async () => {
      const gpu = navigator.gpu;
      if (!gpu) throw new Error("WebGPU unsupported.");
      const adapter = await gpu.requestAdapter();
      if (!adapter) throw new Error("Failed to get WebGPU adapter.");
      return adapter.requestDevice();
    })();
    devicePromise.catch(() => { devicePromise = null; });
  }
  return devicePromise;
}

export function clampTextureSize(cssWidth, cssHeight, dpr, maxSize) {
  const renderScale = Math.min(dpr, maxSize / cssWidth, maxSize / cssHeight);
  return {
    width: Math.max(Math.round(cssWidth * renderScale), 1),
    height: Math.max(Math.round(cssHeight * renderScale), 1),
    renderScale,
  };
}

const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/**
 * Mount a procedural shader on a canvas (ShaderFill without children).
 * @param {HTMLCanvasElement} canvas
 * @param {{setup: Function, render: Function, params: object, manifest?: {isAnimated: boolean}}} shader
 * @returns {Promise<void>}
 */
export async function mountShaderFill(canvas, shader) {
  let device;
  try {
    device = await acquireDevice();
  } catch (e) {
    console.warn("Failed to acquire WebGPU device", e);
    canvas.remove();
    return;
  }
  if (!canvas.isConnected) return;

  const rt = { input: null, output: null, time: 0, deltaTime: 0, frame: 0, renderScale: 1, state: {}, raf: null, last: null, visible: true };
  const format = "rgba8unorm";
  let context;
  try {
    context = canvas.getContext("webgpu");
    if (!context) throw new Error("Failed to get WebGPU context.");
    context.configure({ device, format, alphaMode: "premultiplied" });
    const rect = canvas.getBoundingClientRect();
    const size = clampTextureSize(Math.max(rect.width, 1), Math.max(rect.height, 1), window.devicePixelRatio || 1, device.limits.maxTextureDimension2D);
    canvas.width = size.width;
    canvas.height = size.height;
    rt.renderScale = size.renderScale;
    rt.input = makeInput(device, canvas, format);
  } catch (e) {
    console.warn("Failed to initialize shader runtime", e);
    rt.input?.destroy();
    canvas.remove();
    return;
  }

  const frameState = () => ({
    gpu: { device, context, format }, input: rt.input, output: rt.output, time: rt.time, deltaTime: rt.deltaTime,
    frame: rt.frame, mousePosition: { x: 0, y: 0, down: false }, renderScale: rt.renderScale, params: shader.params, state: rt.state,
  });

  try {
    shader.setup(device, frameState());
  } catch (e) {
    console.warn("Failed to set up effect", e);
    canvas.remove();
    return;
  }

  const animated = Boolean(shader.manifest?.isAnimated) && !reducedMotion();
  const teardown = () => {
    stop();
    io.disconnect();
    ro.disconnect();
    rt.input?.destroy();
    rt.input = null;
  };
  const renderOnce = () => {
    if (!canvas.isConnected) { teardown(); return; }
    try {
      rt.output = context.getCurrentTexture();
      shader.render(device, frameState());
    } catch (e) {
      console.warn("Failed to render effect", e); // transient, as in the original runtime
    }
  };
  const tick = (ts) => {
    rt.raf = null;
    if (!canvas.isConnected) { teardown(); return; }
    rt.deltaTime = rt.last == null ? 0 : ts - rt.last;
    rt.last = ts;
    rt.time += rt.deltaTime;
    rt.frame += 1;
    renderOnce();
    if (animated && rt.visible) rt.raf = requestAnimationFrame(tick);
  };
  const start = () => { if (rt.raf == null) { rt.last = null; rt.raf = requestAnimationFrame(tick); } };
  function stop() { if (rt.raf != null) cancelAnimationFrame(rt.raf); rt.raf = null; rt.last = null; }

  const io = new IntersectionObserver(([entry]) => {
    rt.visible = entry.isIntersecting;
    if (rt.visible && animated) start(); else stop();
  });
  io.observe(canvas);

  const ro = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const box = entry.devicePixelContentBoxSize?.[0] || entry.contentBoxSize?.[0];
      const dpr = entry.devicePixelContentBoxSize ? 1 : window.devicePixelRatio || 1;
      const w = box?.inlineSize ?? entry.contentRect.width;
      const h = box?.blockSize ?? entry.contentRect.height;
      if (!w || !h) continue;
      const size = clampTextureSize(w, h, dpr, device.limits.maxTextureDimension2D);
      canvas.width = size.width;
      canvas.height = size.height;
      rt.renderScale = size.renderScale;
    }
    rt.input?.destroy();
    try {
      rt.input = makeInput(device, canvas, format);
    } catch (e) {
      console.warn("Failed to create texture after resizing", e);
      return;
    }
    if (!animated || rt.raf == null) renderOnce();
  });
  try { ro.observe(canvas, { box: "device-pixel-content-box" }); } catch { ro.observe(canvas); }

  renderOnce();
  if (animated) start();
}

function makeInput(device, canvas, format) {
  return device.createTexture({
    label: "Shader runtime input texture",
    size: [canvas.width, canvas.height, 1],
    format,
    usage: GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.COPY_DST | GPUTextureUsage.RENDER_ATTACHMENT,
  });
}
