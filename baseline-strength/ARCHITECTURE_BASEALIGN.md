# BaseAlign Architecture (Dual-Purpose System)

**Mission:**
1.  **Automated Content Engine (ACE):** Auto-clip, analyze, and post raw/educational content to IG (Kelly Starrett/MobilityWOD vibe).
2.  **Philosophical Brain:** House the specific programming/auto-regulation logic for AI coaching.

## 1. The Knowledge Base (The Brain)
*   **01_core_philosophy.md:** "Training in Exile," "Minimum Effective Dose."
*   **02_programming_and_autoregulation.md:** Sliding scale volume (3-5x5 vs 3-5x10) based on readiness.

## 2. Automated Content Engine (ACE) Workflow

### A. Capture & Clip (The "Smart Tripod")
*   **Input:** Phone on tripod. Continuous recording or "Session Mode."
*   **Logic (Video Clipping):**
    *   **Motion Detection (Pixel Diff):** Detects significant frame changes (The Lifter enters frame / The Bar moves).
    *   **Audio Triggers:** Detects loud noises (Racking the bar, "Up!", plate clamor) to mark end-of-set.
    *   **Heuristic:** "Keep clips where motion > threshold for > 5 seconds."
*   **Output:** Generates `clip_01.mp4`, `clip_02.mp4` (Trimmed to the working set).

### B. Vision Analysis & Copywriting
*   **Vision Model:** GPT-4o / Gemini 1.5 Pro.
*   **Analysis Prompt:**
    *   "Identify the movement (e.g., Squat, 90/90 Stretch)."
    *   "Analyze form quality (Gritty/Raw vibe)."
    *   "Draft caption using '02_programming_and_autoregulation.md'. If it's a heavy squat, mention the 3-5x5 sliding scale. If it's mobility, mention 'Training in Exile'."
*   **Style:** Educational, raw, high-level rehab knowledge.

### C. The Publishing Engine
*   **User Action:** Review Draft -> **"Approve"**.
*   **System Action:** Auto-post to Instagram via Graph API.

## 3. Tech Stack
*   **App:** React Native (Expo) + `ffmpeg-kit` (Smart Clipping).
*   **AI:** OpenAI GPT-4o (Vision + Text).
*   **Backend:** Supabase (Auth, Storage, DB).
*   **Social:** Instagram Graph API.
