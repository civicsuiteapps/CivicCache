#!/usr/bin/env python3
"""
Simple Flask webhook server for n8n to trigger Cache pipeline scripts.
Runs the appropriate pipeline script based on the project parameter.
"""

from flask import Flask, request, jsonify
import subprocess
import sys
import os
from pathlib import Path

app = Flask(__name__)

# Pipeline script paths
PIPELINES = {
    'judicial': '/Users/abrooks/GIT/JudicialCache/judicial_pipeline.py',
    'executive': '/Users/abrooks/GIT/ExecutiveCache/executive_pipeline.py',
    'civic': '/Users/abrooks/GIT/CivicCache/civic_pipeline.py'
}

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'server': 'n8n-cache-webhook',
        'version': '1.0'
    })

@app.route('/run-pipeline', methods=['POST'])
def run_pipeline():
    """
    Execute a pipeline script based on project parameter.

    Expected JSON payload:
    {
        "project": "judicial" | "executive" | "civic"
    }
    """
    try:
        data = request.get_json()
        project = data.get('project')

        if not project:
            return jsonify({
                'success': False,
                'error': 'Missing "project" parameter in request body'
            }), 400

        if project not in PIPELINES:
            return jsonify({
                'success': False,
                'error': f'Invalid project: {project}. Must be one of: judicial, executive, civic'
            }), 400

        pipeline_script = PIPELINES[project]

        # Check if script exists
        if not os.path.exists(pipeline_script):
            return jsonify({
                'success': False,
                'error': f'Pipeline script not found: {pipeline_script}'
            }), 500

        # Execute the pipeline script
        print(f"\n{'='*70}")
        print(f"Executing {project} pipeline: {pipeline_script}")
        print(f"{'='*70}\n")
        sys.stdout.flush()

        result = subprocess.run(
            ['python3', pipeline_script],
            # Don't capture - let output flow to terminal in real-time
            stdout=sys.stdout,
            stderr=sys.stderr,
            timeout=7200  # 2 hour timeout
        )

        success = result.returncode == 0

        print(f"\n{'='*70}")
        print(f"Pipeline {project} finished with exit code: {result.returncode}")
        print(f"{'='*70}\n")
        sys.stdout.flush()

        response = {
            'success': success,
            'project': project,
            'exit_code': result.returncode,
            'message': 'Pipeline completed successfully' if success else 'Pipeline failed'
        }

        status_code = 200 if success else 500
        return jsonify(response), status_code

    except subprocess.TimeoutExpired:
        return jsonify({
            'success': False,
            'error': 'Pipeline execution timeout (2 hours)',
            'project': project
        }), 500

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

if __name__ == '__main__':
    print("Starting n8n Cache Webhook Server...")
    print(f"Available pipelines: {list(PIPELINES.keys())}")
    print("Listening on http://0.0.0.0:5002")
    print("\nEndpoints:")
    print("  GET  /health       - Health check")
    print("  POST /run-pipeline - Execute pipeline (requires 'project' in JSON body)")

    app.run(host='0.0.0.0', port=5002, debug=False)
