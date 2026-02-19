# Vocab Master

![License](https://img.shields.io/badge/license-AGPLv3-blue.svg) ![Python](https://img.shields.io/badge/python-3.11+-green.svg) ![Status](https://img.shields.io/badge/status-active-success.svg)

> **Note: This project is a personal portfolio showcase. The core service logic and data models have been optimized for a specific cloud environment. Unauthorized commercial deployment or redistribution is strictly prohibited.**

Vocab Master is an intelligent English vocabulary assistant on Telegram. Unlike traditional flashcard apps, it leverages **AI-generated dynamic contexts** combined with the **Ebbinghaus Forgetting Curve (SM-2 Algorithm)** to solve the problem of "memorizing and forgetting."

---

## 📋 Recent Updates

### v0.3 — Multi-Language & Vocab Isolation (2026-02)
- **Multi-language learning**: Users can study multiple target languages simultaneously and switch the active language via `/language`. Each language has its own independent word list.
- **Vocab isolation by native language**: The vocab book is now isolated by `(target_language × native_language)`. Users studying English with Chinese vs. Japanese annotations maintain completely separate books — quiz distractors will never mix across native languages.
- **Localized UI (i18n)**: All bot responses, quiz prompts, and feedback messages are rendered in the user's native language (zh / en / ja / ko / de / fr / es / pt / it / ru).
- **New-user onboarding flow**: First-time users are guided through selecting their native language → learning language → timezone.

### v0.2 — SM-2 Upgrade & Notifications (2026-01)
- **3-outcome SM-2**: Quiz results now support three outcomes — *Correct* / *Blurry* / *Forgot* — replacing binary right/wrong for finer-grained interval control.
- **Notification settings** (`/settings`): Toggle push notifications on/off and configure a preferred daily reminder window.
- **New commands**: `/practice`, `/language`, `/streak`, `/stats`, `/search`, `/update`, `/delete`, `/export`, `/timezone`, `/settings`.
- **Vocab detail & inline edit**: Tap any word in `/vocab` to view full details and edit POS / definition / example sentence.

---

## ✨ Key Features

### 🧠 Dynamic Contextual Learning
Traditional vocabulary books only offer static example sentences. Vocab Master uses LLMs (Large Language Models) to generate content in real-time:
- **Dynamic Cloze Tests**: The AI generates fresh example sentences based on the word's part of speech and meaning, creating a cloze test that forces users to recall the word through context.
- **Scenario Reconstruction**: Users can input any sentence, and the Bot automatically extracts key vocabulary to create memory cards.
- **Reverse Lookup**: Input a concept in your native language (e.g., "苟且偷生"), and the AI matches it with the most authentic English expression (e.g., `eke out a living`).

### 📉 Scientific Retention (SM-2 Algorithm)
Built-in improved SM-2 Spaced Repetition Algorithm that dynamically adjusts review intervals based on user feedback (Easy/Blurry/Forgot):
- **Review Queue**: Precision review scheduling down to the minute.
- **Smart Skip**: Words that are fully mastered (Lv7+) are automatically removed from the active review queue.

### 📊 Data Visualization
- **Learning Heatmap**: Tracks learning streaks and daily activity.
- **Mastery Distribution**: Real-time view of vocabulary distribution across different memory stages (Lv0-Lv7).

---

## 🏗️ Technical Architecture

This project adopts a modern Serverless architecture designed for stability and low latency under high concurrency.

- **Bot Framework**: `python-telegram-bot` (Async Mode)
- **Database**: PostgreSQL (Supabase) + Vector Search (Planned)
- **Job Queue**: APScheduler (Distributed Locking)
- **AI Core**: DeepSeek / OpenAI API (Custom Prompt Engineering)
- **Deployment**: Koyeb (Webhook Mode)

### Core Module Design
1. **Scheduler Service**: A distributed task scheduler handling concurrent review notifications per `(user × target_language × native_language)` triplet.
2. **Quiz Generator**: Two quiz types (cloze fill-in / meaning selection); distractor options are strictly scoped to the same native-language vocab book.
3. **State Management**: User session states and quiz progress are synchronized in real-time with the cloud database.
4. **i18n Engine**: All user-facing strings are resolved at runtime from a language table (`bot/i18n.py`), with async caching support.

---

## 🚫 License & Copyright

This project is licensed under the **GNU Affero General Public License v3.0 (AGPLv3)**.

This means:
1. **Non-Commercial Use**: You may read the source code for educational purposes.
2. **Open Source Obligation**: If you run a network service (like a Telegram Bot) based on this project, you **must** disclose your full source code to all users.
3. **No Closed-Source Commercialization**: Any unauthorized commercial modification, deployment, or operation is an infringement.

Copyright © 2026 Ryan (bestian830). All Rights Reserved.

---

## 📅 Roadmap

The project is currently in **Beta**. Future updates will include:

- [ ] **Multimodal Memory**: Integrating TTS voice generation and AI image association.
- [ ] **Social Battles**: Group-based vocabulary PK systems.
- [ ] **Anki Ecosystem**: Two-way synchronization support for `.apkg` format.

---

*If you are interested in the technical implementation of this project or wish to obtain a commercial license, please contact the author via Telegram.*
