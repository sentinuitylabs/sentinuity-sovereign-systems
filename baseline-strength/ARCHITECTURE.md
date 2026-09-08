# BaseLine Strength Architecture

**Mission:** Launch `baselinestrength.com` and `BaseAlign` app powered by a custom AI trained on Zaak's PT philosophies.

## 1. Tech Stack Selection

### A. The Web Platform (`baselinestrength.com`)
*Target: SEO-friendly, fast, content-rich, high-conversion.*

*   **Framework:** **Next.js (React)** - Industry standard for modern web apps. Excellent SEO (Server-Side Rendering), fast performance.
*   **Styling:** **Tailwind CSS** - Rapid UI development, custom design system.
*   **CMS (Content):** **Sanity.io** or **Strapi** (Headless CMS) - To manage blog posts, success stories, and training modules easily.
*   **Hosting:** **Vercel** - Native support for Next.js, global edge network.

### B. The App (`BaseAlign`)
*Target: Mobile-first, biomechanics tracking, client interaction.*

*   **Framework:** **React Native (Expo)** - Build for iOS and Android simultaneously using the same React codebase as the web platform.
*   **Computer Vision (CV):** **TensorFlow.js** or **MediaPipe** (Google) - For real-time pose estimation and biomechanics tracking directly on the phone.
*   **Backend:** **Supabase** (Open Source Firebase alternative) - Database (PostgreSQL), Auth, and Realtime subscriptions.

### C. The Brain (Custom AI Model)
*Target: Personalized coaching, tone-matching, 24/7 guidance.*

*   **Core Model:** **Fine-tuned LLM (OpenAI GPT-4o or Claude 3.5 Sonnet)** via API.
*   **Knowledge Base (RAG):** **Pinecone** (Vector Database).
    *   *Why:* We don't just "train" the model once; we feed it your philosophies, PDF guides, past client interactions, and specific rules. It retrieves this "context" before answering any client.
*   **Orchestration:** **LangChain** or **Vercel AI SDK**.

---

## 2. Integration Architecture

1.  **Client Onboarding:**
    *   User signs up on Web/App.
    *   "The Brain" interviews them (chat interface) to assess goals/injuries based on your protocols.
    *   AI generates a preliminary plan -> You (Zaak) approve/tweak it -> Plan is locked in App.

2.  **Daily Workflow:**
    *   User logs workout in **BaseAlign**.
    *   **CV Feature:** User records a lift. MediaPipe analyzes form (e.g., "Knees caving in").
    *   **AI Feedback:** "Hey [Name], saw the squat video. Your knees are collapsing slightly. Remember Zaak's cue: 'Spread the floor.' Try this warm-up set again."

---

## 3. Immediate Action Plan

1.  **Initialize Project Repo:** Set up Next.js monorepo.
2.  **Knowledge Injection:** Gather your training docs/philosophies to begin building the RAG (Retrieval-Augmented Generation) dataset.
3.  **Prototype UI:** Build the Landing Page and AI Chat Interface.
