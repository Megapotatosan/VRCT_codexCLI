<div align="center">

<picture>
    <source srcset="/docs/img/vrct_logo_white.png" media="(prefers-color-scheme: dark)" width="50%">
    <source srcset="/docs/img/vrct_logo_black.png" media="(prefers-color-scheme: light)" width="50%">
    <img src="/docs/img/vrct_logo.png" alt="VRCT Logo" width="50%">
</picture>

<br>

# VRCT_codexCLI

**A fork of [VRCT](https://github.com/misyaguziya/VRCT) that adds ChatGPT translation through the official Codex CLI — no API key required.**

[![GitHub release](https://img.shields.io/github/v/release/Megapotatosan/VRCT_codexCLI.svg)](https://github.com/Megapotatosan/VRCT_codexCLI/releases)
[![Downloads](https://img.shields.io/github/downloads/Megapotatosan/VRCT_codexCLI/total)](https://github.com/Megapotatosan/VRCT_codexCLI/releases)
[![Licence](https://img.shields.io/github/license/Megapotatosan/VRCT_codexCLI)](https://github.com/Megapotatosan/VRCT_codexCLI/blob/develop/LICENSE)

| **English** | [日本語](/docs/readmes/README.ja.md) | [한국어](/docs/readmes/README.ko.md) | [繁體中文](/docs/readmes/README.zh-Hant.md) |

<div align="left">

---

## Credit — this is a fork

VRCT is created and maintained by **[みしゃ (misyaguzi)](https://github.com/misyaguziya)** and contributors.
Everything this fork does well is built on their work; this repository only adds one translation provider on top of it.

- **Upstream project: [misyaguziya/VRCT](https://github.com/misyaguziya/VRCT)**
- Upstream documentation: <https://misyaguziya.github.io/VRCT-Docs/>
- Licensed MIT, © 2023 misyaguziya — see [LICENSE](/LICENSE)

Original authors:

- [みしゃ(misyaguzi)](https://github.com/misyaguziya) (Main Development)
- [しいな(Shiina_12siy)](https://twitter.com/Shiina_12siy) (UI/UX, UI multilingual support)
- [レラ](https://github.com/soumt-r) (Technical Advisor)
- [どね](https://twitter.com/done_vrc) (Logo Design)

### Please support the original project

This fork accepts no money. If VRCT is useful to you, support the people who built it:

[BOOTH](https://misyaguziya.booth.pm/items/5155325) · [pixivFANBOX](https://vrct-dev.fanbox.cc) · [Patreon](https://patreon.com/vrct_dev) · [GitHub Sponsors](https://github.com/sponsors/misyaguziya)

**Please do not send bug reports about this fork upstream.** Open an issue [here](https://github.com/Megapotatosan/VRCT_codexCLI/issues) instead.

---

## What is VRCT?

VRCT supports conversations between people who speak different languages by providing chat or voice translation. These features are designed for use within VRChat.

- 💬 **Send chat to VRChat**
- 🌐 **Translation**
- 🎙 **Transcription of audio from microphone**
- 🔈 **Transcription of audio from Speaker**

![](/docs/img/main_window.png)

## What this fork adds

A translation engine called **Codex / ChatGPT** (`Codex_CLI`) that translates using your **ChatGPT account** through the official [Codex CLI](https://www.npmjs.com/package/@openai/codex), instead of an OpenAI API key.

|  | `OpenAI API` (upstream) | `Codex / ChatGPT` (this fork) |
|---|---|---|
| Credential | API key you paste in | ChatGPT login, held by the Codex CLI |
| Billing | OpenAI API usage | your ChatGPT plan |
| Setup | get a key from the OpenAI dashboard | click two buttons |

VRCT never sees, stores, or forwards a ChatGPT credential — login and credential storage belong entirely to the official CLI. If that CLI happens to be signed in with an API key, this engine reports itself as **not connected** rather than silently moving your billing to API usage.

Every other translation engine (CTranslate2, DeepL, OpenAI API, Gemini, Groq, OpenRouter, LM Studio, Ollama) is untouched.

### Using it

1. **Config → Translation → Codex / ChatGPT**
2. Click **Install Codex CLI** if it isn't installed (Node.js LTS is installed automatically if needed)
3. Click **Connect ChatGPT** and complete the official login in your browser
4. Select **Codex / ChatGPT** as the translation engine

You never need to open a terminal, and you never need to know what Node.js, npm or PATH are.

> **This is not local translation.** Unlike CTranslate2, LM Studio and Ollama, text you translate with this engine is sent to OpenAI under your ChatGPT account's data-handling settings.

Full details — architecture, timeouts, privacy, limitations: **[docs/codex_translation.md](/docs/codex_translation.md)**

## Download & Install

Grab the installer from [Releases](https://github.com/Megapotatosan/VRCT_codexCLI/releases) and run it.

> **CPU version only.** The GPU/CUDA package is ~3.4GB, which exceeds GitHub's 2GB release-asset limit, so it is not distributed here. Use [upstream VRCT](https://github.com/misyaguziya/VRCT/releases) if you need the CUDA build.

Building it yourself: [docs/readme_build.md](/docs/readme_build.md).

## Documentation

Everything about VRCT's own features (setup, transcription, OSC, overlays) lives in the upstream docs — this fork changes none of it:

- **[VRCT Documentation](https://misyaguziya.github.io/VRCT-Docs/)**
- [How to Use (YouTube)](https://www.youtube.com/watch?v=rUTad037n8Q)

## Telemetry

VRCT collects anonymous telemetry data via [Aptabase](https://aptabase.com) to help improve the app. The collected data includes app starts, session duration, and feature usage. No personally identifiable information is collected, and translated text is never sent.

You can opt out of telemetry in the app settings at any time. See the [Aptabase Privacy Policy](https://aptabase.com/legal/privacy) for more details.

## Thanks to VRCT's contributors

<a href="https://github.com/misyaguziya/VRCT/graphs/contributors" target="_blank">
  <img src="https://contrib.rocks/image?repo=misyaguziya/VRCT" />
</a>

---
