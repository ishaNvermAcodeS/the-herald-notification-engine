# Agent Reverse-Engineering Log

## 1. Purpose

This document records the clean-room reverse-engineering process used to understand the relevant behavior of the original Novu implementation.

The investigation focused only on the functionality required for The Herald:

- event ingestion
- workflow processing
- recipient fan-out
- digest aggregation
- subscriber preferences
- message delivery
- retry and deduplication behavior

The objective was to derive behavioral requirements and engineering observations without copying implementation code.

---

# 2. Investigation Methodology

The investigation was performed through direct inspection of the local Novu repository.

The analysis followed this sequence:

```text
Repository Exploration
        ↓
Identify Relevant Services
        ↓
Trace Event Ingestion
        ↓
Trace Workflow Execution
        ↓
Trace Digest Processing
        ↓
Trace Preference Evaluation
        ↓
Trace Message Delivery
        ↓
Trace Retry / Deduplication
        ↓
Identify Engineering Gaps
        ↓
Select Clean-Room Fix
        ↓
Select Independent Differentiator
