import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from run_burst_observation_v1 import admissible_quality, aggregate, run
from burst_persistence_v1 import run as experiment
from test_burst_persistence_v1 import study, seed_rows


class ObservationTests(unittest.TestCase):
    def test_batch_integration_fixed_count_and_quality_stop(self):
        for damaged in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); binary = root/'binary'; binary.write_bytes(b'receive-only-test')
                args = SimpleNamespace(output=root/'batch', sessions=2, binary=binary,
                                       capture_code=root, env_file=root/'unused-private-env')
                calls = []
                def execute(cmd, **kwargs):
                    name = Path(cmd[1]).name; calls.append(name)
                    if name == 'run_capture_v1.py':
                        out = Path(cmd[cmd.index('--output-dir')+1]); out.mkdir()
                        (out/'capture.jsonl').write_text('{}\n')
                    elif name == 'capture_quality_v1.py':
                        out = Path(cmd[cmd.index('--output')+1]); out.mkdir()
                        q = {'input': {'start_byte': int(damaged), 'unsealed_tail_bytes_excluded': 0,
                                       'sha256': 'fixture', 'snapshot_bytes': 3},
                             'session_start_ns': 0, 'session_end_ns': 2_000_000_000, 'counts': {},
                             'reconnect_gaps': [], 'decoder_gaps': {}, 'finalized_comparison': {},
                             'native_vs_finalized_metadata': {}, 'slot_numeric_discontinuities': 0}
                        (out/'report.json').write_text(json.dumps(q))
                        (out/'transactions.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in seed_rows()))
                    else:
                        experiment(Path(cmd[cmd.index('--source')+1]), Path(cmd[cmd.index('--output')+1]),
                                   cmd[cmd.index('--population')+1])
                    return SimpleNamespace(returncode=0)
                with patch('run_burst_observation_v1.subprocess.run', side_effect=execute), patch(
                        'run_burst_observation_v1.shutil.disk_usage', return_value=SimpleNamespace(free=20*1024**3)):
                    result = run(args)
                self.assertEqual(result['status'], 'STOPPED_QUALITY' if damaged else 'COMPLETE')
                self.assertEqual(result['completed_analyzed_sessions'], 0 if damaged else 2)
                self.assertEqual(calls.count('run_capture_v1.py'), 1 if damaged else 2)
                self.assertTrue((args.output/'session-01/quality/report.json').exists())
                if not damaged:
                    from verify_burst_batch_v1 import verify
                    verified = verify(args.output, root/'verification', args.output/'session-01/experiment/opportunities.jsonl')
                    self.assertEqual(verified['opportunities'], 1)
                    self.assertEqual(verified['later_same_mint_seeds'], 1)
                    self.assertEqual(verified['prior_cohort_mint_overlap']['overlapping_mints'], 1)
                    self.assertTrue(all(s['replay_artifacts_equal'] for s in verified['verified_sessions']))

    def test_safety_floor_stops_without_starting_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); binary = p/'binary'; binary.write_bytes(b'test')
            args = SimpleNamespace(output=p/'batch', sessions=2, binary=binary, capture_code=p, env_file=p/'unused')
            with patch('run_burst_observation_v1.shutil.disk_usage', return_value=SimpleNamespace(free=1)), patch(
                    'run_burst_observation_v1.subprocess.run') as execute:
                result = run(args)
            self.assertEqual(result['status'], 'STOPPED_SAFETY'); execute.assert_not_called()

    def test_reject_truncation_gaps_and_mismatch_not_sparse_prices(self):
        q = {'input': {'start_byte': 0, 'unsealed_tail_bytes_excluded': 0},
             'session_start_ns': 1, 'session_end_ns': 100, 'counts': {}, 'decoder_gaps': {}}
        self.assertEqual(admissible_quality(q), [])
        for key in ('raw_hash_mismatches', 'normalization_errors', 'writer_sequence_discontinuities'):
            bad = copy.deepcopy(q); bad['counts'][key] = 1
            self.assertIn(key, admissible_quality(bad))
        bad = copy.deepcopy(q); bad['input']['start_byte'] = 1
        self.assertIn('truncated_capture', admissible_quality(bad))
        bad = copy.deepcopy(q); bad['reconnect_gaps'] = [{}]
        self.assertIn('reconnect_gap', admissible_quality(bad))

    def test_repeated_mints_do_not_inflate_independent_population(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = study(seed_rows()).outcomes()
            for n in (1, 2):
                path = root/f'session-{n:02}'/'experiment'; path.mkdir(parents=True)
                (path/'opportunities.jsonl').write_text(json.dumps(rows[0])+'\n')
            result = aggregate(root)
            self.assertEqual(result['completed_analyzed_sessions'], 2)
            self.assertEqual(result['independent_first_mint_seeds'], 1)
            self.assertEqual(result['later_same_mint_seeds_retained_but_not_independent'], 1)
            self.assertEqual(result['arms']['burst']['admitted_target_returns']['missing'], 1)


if __name__ == '__main__': unittest.main()
