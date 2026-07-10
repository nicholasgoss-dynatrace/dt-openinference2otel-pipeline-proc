# dt-openinference2otel-pipeline-proc

A utility that configures a Dynatrace **OpenPipeline** processor to convert [OpenInference](https://github.com/Arize-ai/openinference) (Arize Phoenix) span attributes to the [OpenTelemetry `gen_ai.*`](https://opentelemetry.io/docs/specs/semconv/gen-ai/) semantic conventions used by Dynatrace AI Observability.

## What it does

1. **Creates (or updates) an OpenPipeline pipeline** named `openinference-ai-spans` under the OpenTelemetry Spans ingest source in your Dynatrace environment.
2. **Adds a routing rule** that matches spans containing `openinference.span.kind` and routes them through the pipeline.

The pipeline performs the following transforms:

| OpenInference attribute | → | Dynatrace gen_ai.* attribute |
|---|---|---|
| `openinference.span.kind` | → | `gen_ai.operation.kind` (workflow / tool / agent / retrieval / guardrail / task) |
| `llm.model_name` | → | `gen_ai.request.model` |
| `llm.provider` / `llm.system` | → | `gen_ai.provider.name` |
| `llm.token_count.prompt` | → | `gen_ai.usage.input_tokens` |
| `llm.token_count.completion` | → | `gen_ai.usage.output_tokens` |
| `llm.temperature` / `llm.max_tokens` / `llm.top_p` | → | `gen_ai.request.*` |
| `llm.finish_reason` | → | `gen_ai.response.finish_reasons` |
| `embedding.model_name` | → | `gen_ai.request.model` |
| `reranker.model_name` | → | `gen_ai.request.model` |
| `agent.name` | → | `gen_ai.agent.name` |
| `tool.name` / `tool.description` | → | `gen_ai.tool.*` |
| `validator_name` | → | `gen_ai.guardrail.name` |
| `input.value` / `output.value` | → | `gen_ai.input.messages` / `gen_ai.output.messages` |

It also tags every matched span with `ai.observability.source = "openinference"` and removes the now-redundant OpenInference source attributes.

## Prerequisites

- Python 3.10+
- A Dynatrace environment with OpenPipeline available
- A Dynatrace API token with the **`settings.read`** and **`settings.write`** scopes

## Installation

```bash
pip install -r requirements.txt
```

## Usage

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
# edit .env with your DT_ENDPOINT and DT_API_TOKEN
```

Then run:

```bash
python setup_pipeline.py
```

### Options

| Flag | Description |
|---|---|
| `--dry-run` | Print the API payloads without making any changes |
| `--delete` | Remove the pipeline and routing rule |
| `--skip-routing` | Manage only the pipeline; skip the routing rule |

### Examples

```bash
# Preview what would be applied
python setup_pipeline.py --dry-run

# Apply to your environment
python setup_pipeline.py

# Remove everything
python setup_pipeline.py --delete
```

## Configuration

The pipeline definition lives in [`openpipeline_config.yaml`](./openpipeline_config.yaml). You can edit that file to adjust, enable, or disable individual processors before running the script.

## References

- [Dynatrace AI Observability – OpenInference setup](https://docs.dynatrace.com/docs/observe/dynatrace-for-ai-observability/get-started/openinference)
- [OpenInference semantic conventions](https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md)
- [Reference pipeline YAML](https://github.com/dynatrace-oss/dynatrace-ai-agent-instrumentation-examples/blob/main/openai/openinference/openpipeline-openinference.yaml)
