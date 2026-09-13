import tempfile
import unittest
from pathlib import Path

import ay_midi


def midi_track(events):
    payload=b"".join(events)+b"\x00\xff\x2f\x00"
    return b"MTrk"+len(payload).to_bytes(4,"big")+payload


class InstrumentPatchTests(unittest.TestCase):
    def test_general_midi_families_cover_all_programs(self):
        expected={
            0:"piano", 8:"chromatic", 16:"organ", 24:"guitar",
            32:"bass", 40:"strings", 48:"ensemble", 56:"brass",
            64:"reed", 72:"pipe", 80:"lead", 88:"pad",
            96:"effects", 104:"ethnic", 112:"percussive", 120:"sfx",
        }
        self.assertEqual({program:ay_midi.family(program) for program in expected},expected)

    def test_auto_patches_have_distinct_envelopes(self):
        piano=ay_midi.Note(0,1000,60,127,0,0)
        organ=ay_midi.Note(0,1000,60,127,0,16)
        # After 20 frames a piano has decayed while an organ remains sustained.
        self.assertLess(
            ay_midi.patch_volume(piano,200,210,10),
            ay_midi.patch_volume(organ,200,210,10),
        )

    def test_slow_attack_rises_without_becoming_silent(self):
        strings=ay_midi.Note(0,1000,60,127,0,40)
        first=ay_midi.patch_volume(strings,0,10,10)
        later=ay_midi.patch_volume(strings,70,80,10)
        self.assertGreaterEqual(first,1)
        self.assertGreater(later,first)

    def test_short_notes_are_not_buried_by_slow_patch(self):
        strings=ay_midi.Note(0,10,60,127,0,40)
        volume=ay_midi.patch_volume(strings,0,10,10)
        self.assertGreaterEqual(volume,10)

    def test_lead_pitch_modulation_changes_period(self):
        lead=ay_midi.Note(0,2000,69,127,0,80)
        values={
            ay_midi.patch_period(lead,frame*10,(frame+1)*10,10)
            for frame in range(8,40)
        }
        self.assertGreater(len(values),1)

    def test_plain_mode_is_uncoloured_square_wave(self):
        lead=ay_midi.Note(0,2000,69,127,0,80)
        periods={
            ay_midi.patch_period(lead,frame*10,(frame+1)*10,10,"plain")
            for frame in range(40)
        }
        volumes={
            ay_midi.patch_volume(lead,frame*10,(frame+1)*10,10,"plain")
            for frame in range(40)
        }
        self.assertEqual(periods,{ay_midi.period(69)})
        self.assertEqual(volumes,{15})

    def test_active_midi_cells_never_turn_all_tones_off(self):
        notes=[ay_midi.Note(0,100,60,100,0,40)]
        raw,_=ay_midi.frames_for(notes,100,"off",500000,"smart","auto")
        frames=[raw[i:i+14] for i in range(0,len(raw)-1,14)]
        # The final padding frame is a real rest; every source-note frame before
        # it must retain at least one audible AY tone.
        for regs in frames[:-1]:
            self.assertTrue(any(regs[8+i] and not (regs[7]&(1<<i)) for i in range(3)))
        self.assertEqual(frames[-1][7]&7,7)

    def test_program_state_does_not_leak_between_format_one_tracks(self):
        header=b"MThd"+(6).to_bytes(4,"big")+b"\x00\x01\x00\x02\x00\x60"
        lead=midi_track((
            b"\x00\xc0\x50",       # program 80: synth lead
            b"\x00\x90\x3c\x64", # C4 on
            b"\x60\x80\x3c\x00", # C4 off
        ))
        piano=midi_track((
            b"\x00\x90\x40\x64", # E4 on, default program 0
            b"\x60\x80\x40\x00", # E4 off
        ))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"programs.mid"
            path.write_bytes(header+lead+piano)
            _,notes,_=ay_midi.read_midi(path)
        self.assertEqual({note.program for note in notes},{0,80})

    def test_effect_noise_never_overrides_channel_ten_noise(self):
        effect=ay_midi.Note(0,100,72,100,0,96)
        snare=ay_midi.Note(0,100,38,127,9,0)
        raw,_=ay_midi.frames_for([effect,snare],100,"noise",500000,"smart","auto")
        first=raw[:14]
        self.assertEqual(first[6],ay_midi.drum_profile(38)[0])


if __name__=="__main__":
    unittest.main()
