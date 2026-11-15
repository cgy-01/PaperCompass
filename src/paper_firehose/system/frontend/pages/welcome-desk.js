import { $, esc } from "../components/ui.js";

// Illustrative training output supplied for the landing page, never a live run.
const trainingLines = [
  "$ python train.py --model llama3-finetune --lr 2e-5",
  "Epoch 1/5 loss: 2.341 val_loss: 2.198 acc: 0.61",
  "Epoch 2/5 loss: 1.876 val_loss: 1.754 acc: 0.71",
  "Epoch 3/5 loss: 1.423 val_loss: 1.389 acc: 0.78",
  "Epoch 4/5 loss: 1.102 val_loss: 1.087 acc: 0.83",
  "Epoch 5/5 loss: 0.891 val_loss: 0.904 acc: 0.87",
  "✓ Checkpoint saved → ./ckpt/llama3-ft-ep5.pt",
  "$ wandb sync ./runs/llama3-finetune-0226",
  'Syncing run "llama3-ft-0226" to W&B ...',
  "$ python eval.py --split test --batch 32",
  "Loading checkpoint from ./ckpt/llama3-ft-ep5.pt",
  "Running evaluation on 4,218 samples ...",
  "BLEU-4: 38.7 ROUGE-L: 0.621 BERTScore: 0.891",
  '$ git add . && git commit -m "feat: add LoRA adapter"',
  "[main 3f8a2c1] feat: add LoRA adapter — 4 files changed",
  "$ python train.py --model llama3-finetune --lr 2e-5",
  "Epoch 1/5 loss: 2.341 val_loss: 2.198 acc: 0.61",
  "Epoch 2/5 loss: 1.876 val_loss: 1.754 acc: 0.71",
  "Epoch 3/5 loss: 1.423 val_loss: 1.389 acc: 0.78",
  "Epoch 4/5 loss: 1.102 val_loss: 1.087 acc: 0.83",
];

function drawLossCurve() {
  const canvas = $("#welcomeLossCurve");
  const width = canvas.clientWidth,
    height = canvas.clientHeight;
  if (!width || !height) return;
  const ratio = Math.min(devicePixelRatio || 1, 2);
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  const left = width * 0.22,
    right = width * 0.8;
  const bottom = height - 7,
    top = 8;
  const color = getComputedStyle(canvas).color;
  ctx.strokeStyle = color;
  ctx.globalAlpha = 0.1;
  ctx.lineWidth = 0.7;
  for (const fraction of [0.25, 0.55, 0.85]) {
    const y = top + (bottom - top) * fraction;
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(right, y);
    ctx.stroke();
  }
  // Reference silhouette: smooth, flattening curve with a soft area beneath it.
  const curve = () => {
    ctx.moveTo(left, bottom);
    ctx.bezierCurveTo(
      left + (right - left) * 0.23,
      bottom - 8,
      left + (right - left) * 0.12,
      top + 4,
      right,
      top,
    );
  };
  const fill = ctx.createLinearGradient(0, top, 0, bottom);
  fill.addColorStop(0, color);
  fill.addColorStop(1, "transparent");
  ctx.globalAlpha = 0.22;
  ctx.beginPath();
  curve();
  ctx.lineTo(right, bottom);
  ctx.closePath();
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.globalAlpha = 0.95;
  ctx.beginPath();
  curve();
  ctx.lineWidth = 1.5;
  ctx.stroke();
}

let bound = false;
export function renderWelcomeDesk(recent30) {
  if (!bound) {
    bound = true;
    const lines = trainingLines
      .map((line) => {
        const tone = line.startsWith("$")
          ? "command"
          : line.startsWith("✓") || /^\[[^\]]+ [a-f0-9]+\]/.test(line)
            ? "success"
            : "output";
        return `<span class="welcome-log-${tone}">${esc(line)}</span>`;
      })
      .join("");
    // Equal-height copies make translateY(-50%) a seamless reset, without timers.
    $("#welcomeTrainingLog").innerHTML =
      `<div class="welcome-log-track" aria-hidden="true"><pre><code>${lines}</code></pre><pre><code>${lines}</code></pre></div>`;
    if (globalThis.ResizeObserver) {
      const observer = new ResizeObserver(drawLossCurve);
      observer.observe($("#welcomeLossCurve"));
    } else {
      addEventListener("resize", drawLossCurve);
    }
  }
  drawLossCurve();
  if (Number.isInteger(recent30) && recent30 >= 0) {
    $("#welcomeRecentCount").textContent =
      `近 30 天发表 · ${recent30.toLocaleString()} 篇`;
  }
}
