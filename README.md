# Vocab Master

![License](https://img.shields.io/badge/license-AGPLv3-blue) ![Python](https://img.shields.io/badge/python-3.11+-green) ![Status](https://img.shields.io/badge/status-beta-orange)

> **Note:** This project is a personal portfolio showcase. The core service logic and data models have been optimized for a specific cloud environment. Unauthorized commercial deployment or redistribution is strictly prohibited.

Vocab Master is an intelligent English vocabulary assistant on Telegram. Unlike traditional flashcard apps, it leverages AI-generated dynamic contexts combined with the Ebbinghaus Forgetting Curve (SM-2 Algorithm) to solve the problem of "memorizing and forgetting."

---

## 📋 Recent Updates

### v0.3 — Multi-Language & Vocab Isolation (2026-02)
- **Multi-language learning**: Users can study multiple target languages simultaneously and switch the active language via `/language`. Each language has its own independent word list.
- **Vocab isolation by native language**: The vocab book is now isolated by `(target_language × native_language)`. A user studying English with Chinese annotations and another with Japanese annotations maintain completely separate books — distractor options in quizzes will never mix across native languages.
- **Localized UI (i18n)**: All bot responses, quiz prompts, and feedback messages are rendered in the user's native language (supports zh / en / ja / ko / de / fr / es / pt / it / ru).
- **New onboarding flow**: First-time users are guided through selecting their native language → learning language → timezone before starting.

### v0.2 — SM-2 Upgrade & Notifications (2026-01)
- **3-outcome SM-2**: Quiz results now support three outcomes — *Correct* / *Blurry* / *Forgot* — replacing the previous binary right/wrong, giving finer-grained control over review intervals.
- **Notification settings** (`/settings`): Users can toggle push notifications on/off and configure their preferred daily reminder window.
- **New commands**: `/practice` (free practice without SM-2 scheduling), `/stats` (mastery distribution), `/streak` (learning streak), `/search`, `/update`, `/delete`, `/export`, `/timezone`.
- **Vocab detail & edit**: Tap any word in `/vocab` to view full details; edit POS / definition / example sentence inline.

---

## ✨ Key Features

### 🧠 Dynamic Contextual Learning
Traditional vocabulary books only offer static example sentences. Vocab Master uses LLMs to generate content in real-time:

- **Dynamic Cloze Tests**: The AI generates fresh example sentences based on the word's part of speech and meaning, creating a cloze test that forces users to recall the word through context.
- **Scenario Reconstruction**: Users can input any sentence, and the Bot automatically extracts key vocabulary to create memory cards.
- **Reverse Lookup**: Input a concept in your native language (e.g., "苟且偷生"), and the AI matches it with the most authentic target-language expression.

### 📉 Scientific Retention (SM-2 Algorithm)
Built-in improved SM-2 Spaced Repetition Algorithm that dynamically adjusts review intervals based on user feedback (**Easy / Blurry / Forgot**):

- **Review Queue**: Precision review scheduling down to the minute.
- **Smart Skip**: Words that are fully mastered (Lv7+) are automatically removed from the active review queue.

Review interval schedule: **1 → 2 → 4 → 7 → 14 → 30 → 90 → 3650 days**

### 📊 Data Visualization

- **Learning Heatmap**: Tracks learning streaks and daily activity.
- **Mastery Distribution**: Real-time view of vocabulary distribution across different memory stages (Lv0–Lv7).

---

## 🏗️ Technical Architecture

This project adopts a modern Serverless architecture designed for stability and low latency under high concurrency.

| Layer | Technology |
|-------|-----------|
| Bot Framework | python-telegram-bot v20 (Async Mode) |
| Database | PostgreSQL (Supabase) |
| Job Queue | APScheduler (AsyncIOScheduler) |
| AI Core | DeepSeek / OpenAI-compatible API |
| Deployment | Koyeb (Webhook Mode) |
| Runtime | Python 3.11+ |

### Core Module Design

- **Scheduler Service**: Distributed task scheduler handling concurrent review notifications per `(user × target_language × native_language)` triplet.
- **Quiz Generator**: Two quiz types (cloze fill-in / meaning selection); distractor options are strictly scoped to the same native-language vocab book to avoid cross-language contamination.
- **State Management**: User session states and quiz progress are synchronized in real-time with the cloud database.
- **i18n Engine**: All user-facing strings are resolved at runtime from a language table (`bot/i18n.py`), with async caching support.

---

## 🚫 License & Copyright

This project is licensed under the **GNU Affero General Public License v3.0 (AGPLv3)**.

This means:

- **Non-Commercial Use**: You may read the source code for educational purposes.
- **Open Source Obligation**: If you run a network service (like a Telegram Bot) based on this project, you must disclose your full source code to all users.
- **No Closed-Source Commercialization**: Any unauthorized commercial modification, deployment, or operation is an infringement.

Copyright © 2026 Ryan (bestian830). All Rights Reserved.

---

## 📅 Roadmap

The project is currently in **Beta**. Future updates will include:

- [ ] Multimodal Memory: Integrating TTS voice generation and AI image association.
- [ ] Social Battles: Group-based vocabulary PK systems.
- [ ] Anki Ecosystem: Two-way synchronization support for `.apkg` format.

If you are interested in the technical implementation of this project or wish to obtain a commercial license, please contact the author via Telegram.
