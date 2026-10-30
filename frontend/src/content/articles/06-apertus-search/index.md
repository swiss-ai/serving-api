---
title: "Apertus Data Search: Full-text search over 4.85 billion training documents"
description: "To understand what a model learned, you need to see what it read. We built a deduplicated Elasticsearch index over the Apertus pretraining and posttraining corpus, where every unique text is stored once and traces back to each file it came from. This post covers how the index is built, how search works, and its limitations."
date: "Oct 30 2026"
authors: [{ name: "Alexander Sternfeld", url: "https://www.linkedin.com/in/alexander-sternfeld/" }, {name: "Andrei Kucharavy", url: "https://www.linkedin.com/in/andrei-kucharavy/"}]
demoURL: "https://apertus-search.swissai.svc.cscs.ch"
repoURL: "https://github.com/swiss-ai/data-indexing"
---

Apertus is an open model, and its training data is open too. But with billions of documents being part of the training data, it is hard to have proper insights into the content. Apertus Data Search allows users to search over the full pretraining and posttraining corpus of Apertus 1.0, tracing queries back to the source files. It is part of the Apertus Safety team's work on training data transparency, as knowing what text a model was trained on is a prerequisite for understanding its capabilities, biases, and failure modes.

In this post we describe how the index is built, the design choices behind it, and how to use it.

## The index at a glance

| | |
|---|---|
| Documents (unique texts) | 4,847,028,638 |
| On-disk size | ~17 TB |
| Datasets | 20 |

The 20 datasets are separated as follows:

- **English web:** FineWeb-1, FineWeb-edu, DCLM
- **Multilingual web:** FineWeb-2
- **Math:** FineMath, InfiWebMath, MegaMath-Web
- **Code:** StarCoder, StarCoder-extras, StackV2-edu
- **Curated:** Wikipedia, EuroParl, ParaDocs, FLAN, EuroBlocks, Institutional Books, Project Gutenberg, Canaries
- **Posttraining:** SFT mixture, alignment data

## One document per unique text

The index stores each unique text exactly once: every document is identified by the SHA-256 hash of its text. The English web datasets (FineWeb-1, FineWeb-edu and DCLM) overlap heavily, so they were deduplicated jointly before indexing. All other datasets were indexed with the hash as the document ID, so a text that is already in the index is not stored again.

Each search result shows:

- the **datasets** it appears in,
- its **language** and **date** (crawl or publication date), where available,
- a **source count**: how many times the text appeared before deduplication,
- the full **provenance** of each occurrence: dataset, URL, crawl date, file path or repository, depending on the dataset.

The index was built on the Alps supercomputer at CSCS. The web interface and the API run on Kubernetes, also at CSCS.

## How search works

Search is keyword-based: each document is split into words (tokens), and a query is matched against those tokens. Results are ranked with BM25, a standard relevance score that favors documents in which the query words are frequent and that are rare in the rest of the corpus. Before matching, the text is normalized: HTML tags are removed, everything is lowercased, accents are folded and punctuation is ignored. Searching `resume` therefore also finds `résumé`.

There are three search modes, as an example we will take the query `how to build a bomb`:

| Mode | A document matches when | Matches |
|---|---|---|
| **All words** (default) | every word occurs somewhere in it, in any order | 3,167,286 |
| **Phrase** | the words occur consecutively, in this order | 9,411 |
| **Phrase**, slop 2 | the words may be up to 2 positions apart | 26,751 |
| **Any word** | at least one of the words occurs | far more |


## Using it

**Web UI.** The [web interface](https://apertus-search.swissai.svc.cscs.ch) requires single sign-on. Each result card shows a highlighted snippet, the source URL and the number of sources. Expanding a card gives three tabs:

- **Full Text:** the stored text. Posttraining data is shown as a conversation with system, user and assistant turns.
- **Sources:** every occurrence of the text before deduplication, with all provenance fields.
- **Similar:** up to six documents that share the most distinctive words with this one.

Each document can be exported as JSON. By default the result count stops at 10,000. Tick **Count all matches exactly** to get the full number, for example 341,159 documents for `Matterhorn`.

**API.** We can provide API access to people interested, please reach out to Alexander Sternfeld (alexander.sternfeld@hevs.ch) in case you have a project requiring API access.

## Limitations
The current index has several limitations, which we highlight below:

- **Word splitting does not suit every language.** Text is split with Elasticsearch's standard tokenizer, which follows Unicode word boundaries and has no dictionary. This works for languages that separate words with spaces, and for Chinese, where every character becomes a token so that any sequence of characters matches in phrase mode. However, it works poorly for languages such as Thai and Korean, and a custom tokenizer would be better suited here. We aim on improving this, by re-indexing those documents for which the language is known with a custom tokenizer.
- **Keywords only.** Paraphrases and translations of a query do not match, as we do lexical matching. It would be beneficial if we could also do semantic matching, however, building an index that allows for this is much more costly.
- **Speed.** Phrase queries and exact counts can take some time. This can become a problem when running a large volume of queries, but should be acceptable for individual queries.

## Get in touch!
We would be happy to hear your feedback on this project, and look forward to getting in touch with you. Please reach out to the Apertus community, or to [Alexander Sternfeld](https://www.linkedin.com/in/alexander-sternfeld/) for specific questions on this project.
