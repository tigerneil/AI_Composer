import numpy as np
import os
import midi
import cPickle
from pprint import pprint

import midi_util
import mingus
import mingus.core.chords
import sampling

# location of nottingham sequences
PICKLE_LOC = 'data/nottingham.pickle'
# predefined constants specific to the Nottingham dataset
NOTTINGHAM_MELODY_MAX = 88
NOTTINGHAM_MELODY_MIN = 55
# add one to the range for silence in melody
NOTTINGHAM_MELODY_RANGE = NOTTINGHAM_MELODY_MAX - NOTTINGHAM_MELODY_MIN + 1 + 1
CHORD_BASE = 48
CHORD_BLACKLIST = ['major third', 'minor third', 'perfect fifth']
NO_CHORD = 'NONE'
SHARPS_TO_FLATS = {
    "A#": "Bb",
    "B#": "C",
    "C#": "Db",
    "D#": "Eb",
    "E#": "F",
    "F#": "Gb",
    "G#": "Ab",
}

def prepare_nottingham_pickle(time_step, chord_cutoff=0, filename=PICKLE_LOC, verbose=False):

    data = {}
    store = {}
    chords = {}
    max_seq = 0
    seq_lens = []
    
    for d in ["train", "test", "valid"]:
        print "Parsing {}...".format(d)
        parsed = parse_nottingham_directory("data/Nottingham/{}".format(d), time_step, verbose=verbose)
        metadata = [s[0] for s in parsed]
        seqs = [s[1] for s in parsed]
        data[d] = seqs
        data[d + '_metadata'] = metadata
        lens = [len(s[1]) for s in seqs]
        seq_lens += lens
        max_seq = max(max_seq, max(lens))
        
        for _, harmony in seqs:
            for h in harmony:
                if h not in chords:
                    chords[h] = 1
                else:
                    chords[h] += 1

    avg_seq = float(sum(seq_lens)) / len(seq_lens)

    chords = { c: i for c, i in chords.iteritems() if chords[c] >= chord_cutoff }
    chord_mapping = { c: i for i, c in enumerate(chords.keys()) }
    num_chords = len(chord_mapping)
    store['chord_to_idx'] = chord_mapping
    if verbose:
        print chord_mapping
        print "Number of chords: {}".format(num_chords)
        print "Max Sequence length: {}".format(max_seq)
        print "Avg Sequence length: {}".format(avg_seq)

    def combine(melody, harmony):
        full = np.zeros((melody.shape[0], NOTTINGHAM_MELODY_RANGE + num_chords))

        assert melody.shape[0] == len(harmony)

        # for all melody sequences that don't have any notes, add the empty melody marker (last one)
        for i in range(melody.shape[0]):
            if np.count_nonzero(melody[i, :]) == 0:
                melody[i, NOTTINGHAM_MELODY_RANGE-1] = 1

        # all melody encodings should now have exactly one 1
        for i in range(melody.shape[0]):
            assert np.count_nonzero(melody[i, :]) == 1

        # add all the melodies
        full[:, :melody.shape[1]] += melody

        harmony_idxs = [ chord_mapping[h] if h in chord_mapping else chord_mapping[NO_CHORD] \
                         for h in harmony ]
        harmony_idxs = [ NOTTINGHAM_MELODY_RANGE + h for h in harmony_idxs ]
        full[np.arange(len(harmony)), harmony_idxs] = 1

        # all full encodings should have exactly two 1's
        for i in range(full.shape[0]):
            assert np.count_nonzero(full[i, :]) == 2

        return full

    for d in ["train", "test", "valid"]:
        print "Combining {}".format(d)
        store[d] = [ combine(m, h) for m, h in data[d] ]
        store[d + '_metadata'] = data[d + '_metadata']

    with open(filename, 'w') as f:
        cPickle.dump(store, f, protocol=-1)

    return True

def parse_nottingham_directory(input_dir, time_step, verbose=False):
    files = [ os.path.join(input_dir, f) for f in os.listdir(input_dir)
              if os.path.isfile(os.path.join(input_dir, f)) ] 
    sequences = [ \
        parse_nottingham_to_sequence(f, time_step=time_step, verbose=True) \
        for f in files ]

    if verbose:
        print "Total sequences: {}".format(len(sequences))
        # print "Filtering {} ({})".format(len([x == None for x in sequences]), input_dir)
    
    # filter out the non 2-track MIDI's
    sequences = filter(lambda x: x[1] != None, sequences)

    if verbose:
        print "Total sequences left: {}".format(len(sequences))

    return sequences

def parse_nottingham_to_sequence(input_filename, time_step, verbose=False):
    sequence = []
    pattern = midi.read_midifile(input_filename)

    metadata = {
        "path": input_filename,
        "name": input_filename.split("/")[-1].split(".")[0]
    }

    # Most nottingham midi's have 3 tracks. metadata info, melody, harmony
    # throw away any tracks that don't fit this
    if len(pattern) != 3:
        if verbose:
            "Skipping track with {} tracks".format(len(pattern))
        return (metadata, None)

    # ticks_per_quarter = -1
    for msg in pattern[0]:
        if isinstance(msg, midi.TimeSignatureEvent):
            metadata["ticks_per_quarter"] = msg.get_metronome()
            ticks_per_quarter = msg.get_metronome()

    if verbose:
        print "{}".format(input_filename)
        print "Track resolution: {}".format(pattern.resolution)
        print "Number of tracks: {}".format(len(pattern))
        print "Time step: {}".format(time_step)
        print "Ticks per quarter: {}".format(ticks_per_quarter)

    # Track ingestion stage
    track_ticks = 0

    melody_notes, melody_ticks = midi_util.ingest_notes(pattern[1])
    harmony_notes, harmony_ticks = midi_util.ingest_notes(pattern[2])

    track_ticks = midi_util.round_tick(max(melody_ticks, harmony_ticks), time_step)
    if verbose:
        print "Track ticks (rounded): {} ({} time steps)".format(track_ticks, track_ticks/time_step)
    
    melody_sequence = midi_util.round_notes(melody_notes, track_ticks, time_step, 
                                  R=NOTTINGHAM_MELODY_RANGE, O=NOTTINGHAM_MELODY_MIN)

    for i in range(melody_sequence.shape[0]):
        if np.count_nonzero(melody_sequence[i, :]) > 1:
            if verbose:
                print "Double note found: {}: {} ({})".format(i, np.nonzero(melody_sequence[i, :]), input_filename)
            return (metadata, None)

    harmony_sequence = midi_util.round_notes(harmony_notes, track_ticks, time_step)

    harmonies = []
    for i in range(harmony_sequence.shape[0]):
        notes = np.where(harmony_sequence[i] == 1)[0]
        if len(notes) > 0:
            notes_shift = [ mingus.core.notes.int_to_note(h%12) for h in notes]
            # notes_shift = list(set(notes_shift)) # remove duplicates
            chord = mingus.core.chords.determine(notes_shift, shorthand=True)
            if len(chord) == 0:
                # try flat combinations
                notes_shift = [ SHARPS_TO_FLATS[n] if n in SHARPS_TO_FLATS else n for n in notes_shift]
                chord = mingus.core.chords.determine(notes_shift, shorthand=True)
            if len(chord) == 0:
                if verbose:
                    print "Could not determine chord: {} ({}, {}), defaulting to last steps chord" \
                          .format(notes_shift, input_filename, i)
                if len(harmonies) > 0:
                    harmonies.append(harmonies[-1])
                else:
                    harmonies.append(NO_CHORD)
            else:
                # TODO: fix hack that removes 11ths
                if chord[0].endswith("11"):
                    if verbose:
                        print "Encountered 11th note, removing 11th ({})".format(input_filename)
                    chord[0] = chord[0][:-2]

                if chord[0] in CHORD_BLACKLIST:
                    harmonies.append(NO_CHORD)
                else:
                    harmonies.append(chord[0])
        else:
            harmonies.append(NO_CHORD)

    return (metadata, (melody_sequence, harmonies))

class NottinghamMidiWriter(midi_util.MidiWriter):

    def __init__(self, chord_to_idx, verbose=False):
        super(NottinghamMidiWriter, self).__init__(verbose)
        self.idx_to_chord = { i: c for c, i in chord_to_idx.items() }
        self.note_range = NOTTINGHAM_MELODY_RANGE + len(self.idx_to_chord)
        print self.idx_to_chord

    def dereference_chord(self, idx):
        if idx not in self.idx_to_chord:
            raise Exception("No chord index found: {}".format(idx))
        shorthand = self.idx_to_chord[idx]
        if shorthand == NO_CHORD:
            return []
        chord = mingus.core.chords.from_shorthand(shorthand)
        return [ CHORD_BASE + mingus.core.notes.note_to_int(n) for n in chord ]

    def note_on(self, val, tick):
        if val >= NOTTINGHAM_MELODY_RANGE:
            notes = self.dereference_chord(val - NOTTINGHAM_MELODY_RANGE)
        else:
            # if note is the top of the range, then it stands for gap in melody
            if val == NOTTINGHAM_MELODY_RANGE - 1:
                notes = []
            else:
                notes = [NOTTINGHAM_MELODY_MIN + val]

        # print 'turning on {}'.format(notes)
        for note in notes:
            self.track.append(midi.NoteOnEvent(tick=tick, pitch=note, velocity=70))
            tick = 0 # notes that come right after each other should have zero tick

        return tick

    def note_off(self, val, tick):
        if val >= NOTTINGHAM_MELODY_RANGE:
            notes = self.dereference_chord(val - NOTTINGHAM_MELODY_RANGE)
        else:
            notes = [NOTTINGHAM_MELODY_MIN + val]

        # print 'turning off {}'.format(notes)
        for note in notes:
            self.track.append(midi.NoteOffEvent(tick=tick, pitch=note))
            tick = 0

        return tick

class NottinghamSampler(object):

    def __init__(self, chord_to_idx, method = 'sample', harmony_repeat_max = 16, melody_repeat_max = 16, verbose=False):
        self.verbose = verbose 
        self.idx_to_chord = { i: c for c, i in chord_to_idx.items() }
        self.method = method

        self.hlast = 0
        self.hcount = 0
        self.hrepeat = harmony_repeat_max

        self.mlast = 0
        self.mcount = 0
        self.mrepeat = melody_repeat_max 

    def visualize_probs(self, probs):
        if not self.verbose:
            return

        melodies = sorted(list(enumerate(probs[:NOTTINGHAM_MELODY_RANGE])), 
                     key=lambda x: x[1], reverse=True)[:4]
        harmonies = sorted(list(enumerate(probs[NOTTINGHAM_MELODY_RANGE:])), 
                     key=lambda x: x[1], reverse=True)[:4]
        harmonies = [(self.idx_to_chord[i], j) for i, j in harmonies]
        print 'Top Melody Notes: '
        pprint(melodies)
        print 'Top Harmony Notes: '
        pprint(harmonies)

    def sample_notes_static(self, probs):
        top_m = probs[:NOTTINGHAM_MELODY_RANGE].argsort()
        if top_m[-1] == self.mlast and self.mcount >= self.mrepeat:
            top_m = top_m[:-1]
            self.mcount = 0
        elif top_m[-1] == self.mlast:
            self.mcount += 1
        else:
            self.mcount = 0
        self.mlast = top_m[-1]
        top_melody = top_m[-1]

        top_h = probs[NOTTINGHAM_MELODY_RANGE:].argsort()
        if top_h[-1] == self.hlast and self.hcount >= self.hrepeat:
            top_h = top_h[:-1]
            self.hcount = 0
        elif top_h[-1] == self.hlast:
            self.hcount += 1
        else:
            self.hcount = 0
        self.hlast = top_h[-1]
        top_chord = top_h[-1] + NOTTINGHAM_MELODY_RANGE

        chord = np.zeros([len(probs)], dtype=np.int32)
        chord[top_melody] = 1.0
        chord[top_chord] = 1.0
        return chord

    def sample_notes_dist(self, probs):
        idxed = [(i, p) for i, p in enumerate(probs)]

        notes = [n[0] for n in idxed]
        ps = np.array([n[1] for n in idxed])
        r = NOTTINGHAM_MELODY_RANGE

        assert np.allclose(np.sum(ps[:r]), 1.0)
        assert np.allclose(np.sum(ps[r:]), 1.0)

        # renormalize so numpy doesn't complain
        ps[:r] = ps[:r] / ps[:r].sum()
        ps[r:] = ps[r:] / ps[r:].sum()

        melody = np.random.choice(notes[:r], p=ps[:r])
        harmony = np.random.choice(notes[r:], p=ps[r:])

        chord = np.zeros([len(probs)], dtype=np.int32)
        chord[melody] = 1.0
        chord[harmony] = 1.0
        return chord


    def sample_notes(self, probs):
        self.visualize_probs(probs)
        if self.method == 'static':
            return self.sample_notes_static(probs)
        elif self.method == 'sample':
            return self.sample_notes_dist(probs)

def accuracy(raw_probs, test_sequences, config, num_samples=1):
    
    batch_size = config["batch_size"]
    time_batch_len = config["time_batch_len"]
    input_dim = config["input_dim"]

    # reshape probability batches into [time_batch_len * max_time_batches, batch_size, input_dim]
    test_probs = np.concatenate(raw_probs, axis=0)
    print test_probs.shape

    def calc_accuracy():
        total = 0
        melody_correct, harmony_correct = 0, 0
        for seq_idx, seq in enumerate(test_sequences):
            for step_idx in range(seq.shape[0]):
                if step_idx == 0:
                    continue

                idxed = [(n, p) for n, p in enumerate(test_probs[step_idx-1, seq_idx, :])]
                notes = [n[0] for n in idxed]
                ps = np.array([n[1] for n in idxed])
                r = NOTTINGHAM_MELODY_RANGE

                assert np.allclose(np.sum(ps[:r]), 1.0)
                assert np.allclose(np.sum(ps[r:]), 1.0)

                # renormalize so numpy doesn't complain
                ps[:r] = ps[:r] / ps[:r].sum()
                ps[r:] = ps[r:] / ps[r:].sum()

                melody = np.random.choice(notes[:r], p=ps[:r])
                harmony = np.random.choice(notes[r:], p=ps[r:])

                melody_target = np.nonzero(seq[step_idx, :r])[0] 
                assert len(melody_target) == 1
                if melody_target == melody:
                    melody_correct += 1

                harmony_target = np.nonzero(seq[step_idx, r:])[0] + NOTTINGHAM_MELODY_RANGE
                assert len(harmony_target) == 1
                if harmony_target == harmony:
                    harmony_correct += 1

                total += 2

        melody_acc = (float(melody_correct) / float(total)) * 2.0
        harmony_acc = (float(harmony_correct) / float(total)) * 2.0
        total_acc = float(melody_correct + harmony_correct) / float(total)

        return (melody_acc, harmony_acc, total_acc)

    maccs, haccs, taccs = [], [], []
    for i in range(num_samples):
        print "Sample {}".format(i)
        macc, hacc, tacc = calc_accuracy()
        maccs.append(macc)
        haccs.append(hacc)
        taccs.append(tacc)

    print "Melody Accuracy: {}".format(sum(maccs)/len(maccs))
    print "Harmony Accuracy: {}".format(sum(haccs)/len(haccs))
    print "Total Accuracy: {}".format(sum(taccs)/len(taccs))


    # print 'Total Notes: {}'.format(total)
    # print 'Melody Accuracy: {} ({}/{})'.format((float(melody_correct) / float(total)) * 2.0,
    #                                            melody_correct,
    #                                            total / 2)
    # print 'Harmony Accuracy: {} ({}/{})'.format((float(harmony_correct) / float(total)) * 2.0,
    #                                             harmony_correct,
    #                                             total / 2)
    # print 'Total Accuracy: {} ({}/{})'.format(float(melody_correct + harmony_correct) / float(total),
    #                                           melody_correct + harmony_correct,
    #                                           total)

if __name__ == '__main__':

    resolution = 480
    time_step = 120

    prepare_nottingham_pickle(time_step, verbose=True)
    # melody, harm = parse_nottingham_to_sequence("data/Nottingham/train/ashover_simple_chords_3.mid", time_step, verbose=True)
    # pprint(zip(range(len(harm)), harm))

    with open(PICKLE_LOC, 'r') as f:
        p = cPickle.load(f)
    writer = NottinghamMidiWriter(p['chord_to_idx'], verbose=True)
    for i in range(len(p['train'])):
        writer.dump_sequence_to_midi(p['train'][i], '/tmp/{}.midi'.format(p['train_metadata'][i]['name']), time_step, resolution)
    for i in range(len(p['test'])):
        writer.dump_sequence_to_midi(p['test'][i], '/tmp/{}.midi'.format(p['test_metadata'][i]['name']), time_step, resolution)