"""Workflow template handling: token injection + placeholder rejection (FR-4/5)."""
import json

import pytest

from conftest import REPO_DIR
from lib.comfyui import (
    ComfyUIError,
    WorkflowTemplateError,
    _add_ipadapter_reference_chain,
    avatar_reference_paths,
    first_output_file,
    inject,
    load_workflow,
)


def test_inject_replaces_tokens_and_preserves_int_types():
    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "__FRAME_IMAGE__"}},
        "2": {"class_type": "KSampler", "inputs": {"seed": "__SEED__"}},
        "3": {"class_type": "Note", "inputs": {"text": "lora=__LORA_NAME__ here"}},
    }
    result = inject(workflow, {
        "__FRAME_IMAGE__": "frame1.png",
        "__SEED__": 1234,
        "__LORA_NAME__": "avatar_v1.safetensors",
    })
    assert result["1"]["inputs"]["image"] == "frame1.png"
    assert result["2"]["inputs"]["seed"] == 1234          # int, not "1234"
    assert result["3"]["inputs"]["text"] == "lora=avatar_v1.safetensors here"


def test_inject_errors_on_unfilled_tokens():
    workflow = {"1": {"inputs": {"image": "__MYSTERY_TOKEN__"}}}
    with pytest.raises(ComfyUIError, match="__MYSTERY_TOKEN__"):
        inject(workflow, {"__SEED__": 1})


def test_inject_ignores_replacements_absent_from_workflow():
    workflow = {"1": {"inputs": {"seed": "__SEED__"}}}
    result = inject(workflow, {"__SEED__": 7, "__WIDTH__": 720})
    assert result["1"]["inputs"]["seed"] == 7


def test_shipped_workflows_are_real_exports_not_placeholders():
    """Both workflows were built and validated live against ComfyUI on
    2026-07-03 (see HANDOFF.md) — they must never regress back to templates."""
    for name in ("avatar_into_frame.api.json", "wan_animate.api.json"):
        workflow = load_workflow(REPO_DIR / "comfyui" / name)
        assert not workflow.get("__template__")
        assert len(workflow) > 1


def test_missing_workflow_file_gives_setup_instructions(tmp_path):
    with pytest.raises(WorkflowTemplateError, match="Export \\(API\\)"):
        load_workflow(tmp_path / "nope.api.json")


def test_real_export_loads(tmp_path):
    path = tmp_path / "wf.api.json"
    path.write_text(json.dumps({"1": {"class_type": "LoadImage", "inputs": {}}}))
    assert "1" in load_workflow(path)


def test_first_output_file_finds_video_outputs():
    outputs = {
        "9": {"text": ["ignored"]},
        "12": {"gifs": [{"filename": "out.mp4", "subfolder": "", "type": "output"}]},
    }
    info = first_output_file(outputs, kinds=("gifs", "videos", "images"))
    assert info["filename"] == "out.mp4"


def test_first_output_file_errors_when_absent():
    with pytest.raises(ComfyUIError, match="no output file"):
        first_output_file({"1": {"text": ["x"]}}, kinds=("images",))


def test_avatar_reference_paths_uses_configured_refs(make_config, tmp_path):
    from lib.config import load_config

    config_path = make_config(avatar_frame={
        "identity_references": ["./refs/front.png", "./refs/side.png"],
    })
    cfg = load_config(config_path)
    assert avatar_reference_paths(cfg) == [
        (tmp_path / "refs" / "front.png").resolve(),
        (tmp_path / "refs" / "side.png").resolve(),
    ]


def test_ipadapter_chain_adds_multiple_refs_without_rewriting_chain():
    workflow = {
        "14": {"class_type": "KSampler", "inputs": {"model": ["28", 0]}},
        "20": {"class_type": "FaceDetailer", "inputs": {"model": ["28", 0]}},
        "27": {"class_type": "IPAdapterModelLoader", "inputs": {}},
        "28": {
            "class_type": "IPAdapterAdvanced",
            "inputs": {
                "model": ["3", 0],
                "ipadapter": ["27", 0],
                "image": ["26", 0],
                "weight": 0.68,
                "weight_type": "linear",
                "combine_embeds": "concat",
                "start_at": 0.0,
                "end_at": 0.8,
                "embeds_scaling": "V only",
                "clip_vision": ["29", 0],
            },
        },
        "29": {"class_type": "CLIPVisionLoader", "inputs": {}},
        "30": {"class_type": "GrowMaskWithBlur", "inputs": {}},
    }

    result = _add_ipadapter_reference_chain(workflow, ["front.png", "side.png", "selfie.png"])

    assert result["28"]["inputs"]["weight"] == 0.42
    assert result["31"]["class_type"] == "LoadImage"
    assert result["32"]["inputs"]["model"] == ["28", 0]
    assert result["33"]["class_type"] == "LoadImage"
    assert result["34"]["inputs"]["model"] == ["32", 0]
    assert result["14"]["inputs"]["model"] == ["34", 0]
    assert result["20"]["inputs"]["model"] == ["34", 0]
