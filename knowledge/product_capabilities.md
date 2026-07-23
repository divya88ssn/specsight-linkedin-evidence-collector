# Specsight Product Capabilities

## Purpose and source boundary

This document captures the product capabilities Specsight currently provides through the eight documentation pages in the **Working with the spec** section:

1. Features & scenarios
2. Ask Specsight
3. Annotations
4. Changelog
5. Feature map
6. Digests
7. Automatic syncing
8. Export the spec

It intentionally excludes account creation, pricing, organisation administration, GitHub setup, MCP setup, billing, security, and other operational or onboarding documentation.

The purpose of this file is to establish a product-grounded knowledge source for later classification of external market evidence. It describes what Specsight demonstrably enables. It does not yet add ICP assumptions, buying triggers, market pain-point frequency, or speculative value claims.

---

## Product operating model

Specsight creates a living product specification from a connected codebase. The specification is organised into features and plain-language behavioural scenarios. After the initial analysis, releases to the watched branch trigger updates so the specification can remain aligned with the product as its implementation changes.

The eight product areas work together as a system:

```text
Codebase and releases
        ↓
Features and behavioural scenarios
        ↓
Feature relationships and internal flows
        ↓
Human context and annotations
        ↓
Change history
        ↓
Questions, digests, and exports
```

The primary product object is therefore not a traditional manually maintained requirements document. It is a code-derived view of current product behaviour, enriched by human context and maintained through release-triggered updates.

---

# 1. Features and scenarios

## Product function

Specsight organises a product specification into **features**. Each feature is broken into individual **scenarios** describing how the product behaves in a specific situation.

Scenarios use a plain-language structure:

- **Context:** the conditions or state before an interaction
- **Action:** what the user or system does
- **Outcome:** what the product does as a result

A specification can include:

- normal or successful flows;
- edge cases;
- validation behaviour;
- blocked actions;
- error behaviour;
- system-triggered behaviour.

Users can inspect the generated specification and add, edit, or delete scenarios.

## Core capabilities provided

### 1.1 Behaviour-level product representation

Represents the product in terms of externally meaningful behaviour rather than implementation details alone.

### 1.2 Feature-based product organisation

Groups related behaviour into meaningful product capabilities or user-facing feature areas.

### 1.3 Scenario-level behavioural decomposition

Breaks broad features into specific situations that can be read and reviewed independently.

### 1.4 Context-action-outcome clarity

Provides a consistent structure for describing the conditions, interaction, and resulting behaviour of a scenario.

### 1.5 Happy-path, edge-case, and error visibility

Makes non-primary behaviour visible alongside standard flows rather than documenting only the expected path.

### 1.6 Plain-language accessibility

Makes product behaviour understandable to people who do not read the source code.

### 1.7 Human review and correction

Allows people to modify the generated representation when additional clarification or correction is needed.

## Product boundary

This capability describes what the product currently does. It does not, by itself, prove that a scenario is an approved requirement, an executable automated test, or a substitute for strategy, design rationale, customer help content, or roadmap documentation.

---

# 2. Ask Specsight

## Product function

Ask Specsight provides a conversational interface for asking questions about the product specification. Answers are grounded in information already held within the product, including:

- features;
- scenarios;
- changelog entries;
- digests.

The interface provides sources that users can inspect.

## Core capabilities provided

### 2.1 Natural-language product inquiry

Allows users to ask product questions in ordinary language instead of manually navigating the full specification.

### 2.2 Specification-grounded answers

Uses the product specification and its history as the basis for answers.

### 2.3 Source-backed product explanations

Provides supporting sources so users can inspect the material behind an answer.

### 2.4 Cross-feature knowledge access

Allows questions whose answers may span multiple features, scenarios, or historical changes.

### 2.5 Historical product inquiry

Supports questions about how behaviour changed over time when the relevant information exists in the changelog or digests.

### 2.6 Self-service access to product knowledge

Makes product information accessible without requiring every question to be routed through an engineer or another internal expert.

## Product boundary

Ask Specsight is grounded in the information represented inside Specsight. It should not be interpreted as a source for business strategy, customer commitments, undocumented rationale, or external information unless that context has been added to the specification or associated records.

---

# 3. Annotations

## Product function

Annotations are free-text notes attached to individual scenarios. They allow users to add information that cannot be inferred directly from code.

Examples include:

- questions;
- decision rationale;
- caveats;
- links to tickets;
- supporting context;
- clarification for other teams.

Annotations are visible to members of the organisation.

## Core capabilities provided

### 3.1 Human context enrichment

Adds business or organisational context to a code-derived behavioural scenario.

### 3.2 Scenario-level collaboration

Keeps discussion and clarification attached to the exact product behaviour being discussed.

### 3.3 Decision-rationale capture

Preserves explanations for why a behaviour exists or why a decision was made.

### 3.4 Caveat and constraint documentation

Records limitations, exceptions, dependencies, or operational considerations not apparent from implementation alone.

### 3.5 Linked-evidence attachment

Allows related tickets or external references to be associated with a scenario.

### 3.6 Shared organisational visibility

Makes added context available to the wider organisation instead of leaving it in a private conversation or individual memory.

## Product boundary

Annotations are manually supplied context. Their accuracy and completeness depend on contributors. They complement code-derived behaviour; they do not automatically validate whether the annotation is current or correct.

---

# 4. Changelog

## Product function

The changelog provides a chronological record of changes to a project's specification.

It can include:

- changes produced by automatic Specsight syncs;
- manual edits to the specification;
- annotations;
- releases Specsight reviewed where no relevant behavioural change was found.

Change history can be associated with the project, features, or scenarios.

## Core capabilities provided

### 4.1 Product-behaviour change history

Records how the documented behaviour of the product evolves over time.

### 4.2 Release-to-specification traceability

Connects releases and sync activity to changes in the product specification.

### 4.3 Added, changed, and removed behaviour visibility

Shows whether behavioural elements were introduced, modified, or removed.

### 4.4 Human-versus-system change attribution

Distinguishes automatic product-analysis updates from manual human edits where attribution is available.

### 4.5 No-behaviour-change confirmation

Records releases that were analysed without a resulting behavioural specification change.

### 4.6 Feature- and scenario-level history

Makes it possible to inspect the evolution of a particular part of the product rather than only a project-wide release list.

### 4.7 Historical reconstruction

Supports understanding what the product specification showed at different points in its evolution.

## Product boundary

The changelog represents changes detected or recorded within Specsight. It is not necessarily a full engineering audit log, source-control replacement, incident timeline, or complete explanation of why every code change occurred.

---

# 5. Feature map

## Product function

The feature map provides a two-level visual representation of a project:

1. a project-level view of the product's features and the relationships between them;
2. a feature-level view showing the step-by-step flow within a selected feature.

The feature relationship view can help users understand how changes in one area may affect connected product areas.

## Core capabilities provided

### 5.1 Product-wide structural visibility

Shows the set of product features as a connected system rather than an isolated list.

### 5.2 Cross-feature relationship mapping

Represents how one feature leads to, authenticates, triggers, notifies, or otherwise connects with another feature.

### 5.3 Dependency awareness

Helps users identify product areas that may rely on or interact with one another.

### 5.4 Change blast-radius awareness

Supports inspection of which connected feature areas may warrant attention when one part of the product changes.

### 5.5 Feature-level flow visualisation

Shows the ordered internal behavioural flow of an individual feature.

### 5.6 User-journey and system-flow comprehension

Makes multi-step paths easier to understand through a visual model of product behaviour.

## Product boundary

The feature map represents product-behaviour relationships identified in the specification. It should not automatically be treated as a complete technical architecture diagram, service dependency map, data lineage model, or guaranteed impact analysis.

---

# 6. Digests

## Product function

Digests summarise how a project's specification changed across a selected date range.

They can be generated:

- on demand; or
- according to a schedule.

A digest turns multiple specification changes into a consolidated, plain-language update intended for sharing with stakeholders.

## Core capabilities provided

### 6.1 Time-bounded change summarisation

Summarises product-behaviour changes occurring during a chosen period.

### 6.2 On-demand stakeholder reporting

Allows a user to generate a current summary when a specific communication need arises.

### 6.3 Scheduled product-change reporting

Supports recurring summaries without requiring each report to be assembled manually.

### 6.4 Change synthesis

Consolidates multiple scenario- and feature-level changes into a more consumable report.

### 6.5 Added, changed, and removed behaviour communication

Organises release information around meaningful product changes rather than raw engineering activity.

### 6.6 Shareable product updates

Produces information designed to be forwarded to internal or external stakeholders with less rewriting.

## Product boundary

A digest summarises changes represented in Specsight. It is not automatically a marketing release note, legal customer notice, roadmap status report, incident report, or adoption-impact analysis unless a user adapts it for that purpose.

---

# 7. Automatic syncing

## Product function

Specsight watches a selected branch. When a release lands on that branch, Specsight automatically analyses the relevant changes and updates the specification.

After the initial full analysis, release syncs update only the scenarios affected by the new release rather than regenerating the entire specification from scratch.

## Core capabilities provided

### 7.1 Release-triggered specification maintenance

Updates the product specification as new releases are made to the watched branch.

### 7.2 Incremental behavioural re-analysis

Focuses subsequent analysis on affected behaviour rather than repeatedly analysing the complete product.

### 7.3 Code-to-specification alignment

Uses changes in the implementation to keep the behavioural specification current.

### 7.4 Documentation-drift reduction

Reduces reliance on people remembering to update a separate product-behaviour document after every release.

### 7.5 Continuous product-behaviour visibility

Maintains an evolving view of product behaviour across successive releases.

### 7.6 Change-history accumulation

Builds a historical record of product evolution through repeated release syncs.

## Product boundary

Automatic syncing updates the specification based on the watched code branch and the changes Specsight analyses. It does not prove runtime correctness, production adoption, customer value, performance, test coverage, or conformity with original product intent.

---

# 8. Export the spec

## Product function

Specsight allows users to export:

- the full specification; or
- an individual feature.

Supported export formats include:

- PDF;
- Excel/XLSX;
- Markdown.

Digests can also be exported for distribution.

## Core capabilities provided

### 8.1 Portable product specification

Makes specification content usable outside the Specsight interface.

### 8.2 Full-specification export

Allows the complete project specification to be downloaded as an external artifact.

### 8.3 Feature-specific export

Allows a user to share or review one selected product area without exporting the entire project.

### 8.4 Multi-format distribution

Supports different stakeholder and workflow needs through document, spreadsheet, and plain-text formats.

### 8.5 Offline review

Enables product behaviour to be reviewed without requiring live access to the Specsight application.

### 8.6 Existing-workflow interoperability

Allows specification content to be used in documentation, review, audit, handoff, and communication workflows outside Specsight.

## Product boundary

An exported artifact is a point-in-time copy. Unlike the live specification, it does not automatically remain current after subsequent releases unless it is exported again.

---

# Consolidated capability model

The eight documented product areas combine into the following higher-level capability groups.

## A. Product behaviour representation

Supported primarily by:

- Features and scenarios

Enables:

- feature-based organisation;
- scenario-level decomposition;
- context-action-outcome representation;
- happy-path, edge-case, and error-state visibility;
- plain-language product understanding.

## B. Product knowledge access

Supported primarily by:

- Features and scenarios;
- Ask Specsight;
- Feature map.

Enables:

- browsing current product behaviour;
- natural-language questioning;
- source-backed answers;
- visual product exploration;
- cross-feature understanding.

## C. Human context and collaboration

Supported primarily by:

- Annotations;
- manual scenario editing.

Enables:

- business-context enrichment;
- rationale capture;
- scenario-level questions and caveats;
- shared organisational knowledge.

## D. Product change observability

Supported primarily by:

- Changelog;
- Automatic syncing;
- Digests.

Enables:

- release-by-release specification updates;
- change-history visibility;
- added, changed, removed, and unchanged release reporting;
- historical product inquiry;
- recurring stakeholder updates.

## E. Product structure and impact awareness

Supported primarily by:

- Feature map;
- Features and scenarios;
- Changelog.

Enables:

- cross-feature relationship visibility;
- feature-flow visualisation;
- dependency awareness;
- preliminary change blast-radius awareness;
- inspection of connected product areas.

## F. Product knowledge portability

Supported primarily by:

- Export the spec;
- Digests.

Enables:

- external sharing;
- offline review;
- feature-specific or project-wide artifacts;
- integration with existing documentation and stakeholder workflows.

---

# Canonical capability list for later machine classification

The following identifiers are suitable candidates for a later machine-readable taxonomy. They are included here only as normalized names for capabilities directly supported by the documented product functions.

```text
product_behavior_representation
feature_based_specification
scenario_level_behavior_modeling
context_action_outcome_structure
happy_path_visibility
edge_case_visibility
error_behavior_visibility
plain_language_product_understanding
human_specification_review
natural_language_product_inquiry
specification_grounded_answers
source_backed_product_answers
historical_product_inquiry
human_context_enrichment
scenario_level_collaboration
decision_rationale_capture
caveat_and_constraint_capture
linked_evidence_context
product_change_history
release_to_specification_traceability
behavior_change_classification
change_source_attribution
no_behavior_change_confirmation
feature_level_change_history
product_structure_visualization
cross_feature_relationship_mapping
dependency_awareness
change_blast_radius_awareness
feature_flow_visualization
time_bounded_change_summary
on_demand_product_reporting
scheduled_product_reporting
stakeholder_ready_change_communication
release_triggered_specification_sync
incremental_behavior_reanalysis
code_to_specification_alignment
documentation_drift_reduction
continuous_product_behavior_visibility
portable_product_specification
full_specification_export
feature_specific_export
multi_format_distribution
offline_specification_review
```

These identifiers should not yet be treated as market-pain labels. The later classification taxonomy should connect them to problem signals, workflow failure modes, ICPs, buying triggers, exclusions, and evidence-strength rules using the GTM research corpus.

---

# Explicit non-claims

To prevent the downstream classifier from overstating the product, this product knowledge file does **not** claim that Specsight currently provides:

- automatic validation against original product intent;
- automated acceptance testing;
- executable BDD tests;
- runtime monitoring or production telemetry;
- user analytics or adoption measurement;
- source-code quality analysis;
- defect prediction;
- project or sprint management;
- roadmap management;
- customer-feedback collection;
- automatic prioritisation;
- guaranteed impact analysis;
- a complete technical architecture map;
- a replacement for strategy, research, design, support, or customer-facing documentation.

Any future mapping from market evidence to Specsight must distinguish between:

1. a capability Specsight demonstrably provides;
2. a workflow where that capability may be useful;
3. an outcome or ROI hypothesis that still requires market validation.

---

# Source pages

- https://specsight.app/docs/features-and-scenarios
- https://specsight.app/docs/ask-specsight
- https://specsight.app/docs/annotations
- https://specsight.app/docs/changelog
- https://specsight.app/docs/flow-diagram
- https://specsight.app/docs/digests
- https://specsight.app/docs/automatic-syncing
- https://specsight.app/docs/export-specifications
