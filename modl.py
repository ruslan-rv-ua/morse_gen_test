from io import StringIO
import wave
import errno
import struct
import pyaudio
import math
import itertools
from typing import Iterable

TWO_PI = 2 * math.pi
# frame rate in Hertz
FRAME_RATE=44100
# sample width in bytes, 2 = 16 bit
SAMPLE_WIDTH=2
BUFFER_SIZE=100000
COMPRESSION_TYPE='NONE'
COMPRESSION_NAME='no compression'



def tone(frequency=440, min_=-1, max_=1):
    def fixed_tone(frequency):
        period = int(FRAME_RATE / frequency)
        time_scale = TWO_PI / period  # period * scale = 2 * pi
        # precompute fixed tone samples # TODO: what about phase glitches at end?
        samples = [math.sin(i * time_scale) for i in range(period)]
        #! print(f'{len(samples)=}')
        while True:
            for i in range(period):
                yield samples[i]

    gen = fixed_tone(frequency)

    #! return util.normalize(gen, -1, 1, min_, max_)
    return gen


def crop(gens, seconds=5, cropper=None):
	'''
	Crop the generator to a finite number of frames

	Return a generator which outputs the provided generator limited
	to enough samples to produce seconds seconds of audio (default 5s)
	at the provided frame rate.
	'''
	if isinstance( gens, Iterable ):
		gens = (gens,)

	if cropper is None:
		cropper = lambda gen: itertools.islice(gen, 0, int(seconds * FRAME_RATE))

	cropped = [cropper(gen) for gen in gens]
	return cropped[0] if len(cropped) == 1 else cropped



def beep(frequency=440, seconds=0.25):
    #! for sample in util.crop_with_fades(tone(frequency), seconds=seconds):
    for sample in crop(tone(frequency), seconds=seconds):
        yield sample

def synth(freq, angles):
	if isinstance(angles, (int, float)):
		# argument was just the end angle
		angles = [0, angles]
	gen = tone(freq)
	loop = list(itertools.islice(gen, (FRAME_RATE / freq) * (angles[1] / (2.0 * math.pi))))[int((FRAME_RATE / freq) * (angles[0] / (2.0 * math.pi))):]
	while True:
		for sample in loop:
			yield sample

def silence(seconds=None):
	if seconds is not None:
		for i in range(int(FRAME_RATE * seconds)):
			yield 0
	else:
		while True:
			yield 0








def sample(generator, min=-1, max=1, width=SAMPLE_WIDTH):
	'''Convert audio waveform generator into packed sample generator.'''
	# select signed char, short, or in based on sample width
	# fmt = { 1: '<B', 2: '<h', 4: '<i' }[width]
	# return (struct.pack(fmt, int(sample)) for sample in \
		#	normalize(hard_clip(generator, min, max),\
		#		min, max, -2**(width * 8 - 1), 2**(width * 8 - 1) - 1))
	scale = float(2**(width * 8) - 1) / (max - min)
	return (struct.pack('h', int((sample - min) * scale) - 2**(width * 8 - 1)) for sample in generator)

def sample_all(generators, *args, **kwargs):
	'''Convert list of audio waveform generators into list of packed sample generators.'''
	return [sample(gen, *args, **kwargs) for gen in generators]


def interleave(channels):
	'''
	Interleave samples from multiple channels for wave output

	Accept a list of channel generators and generate a sequence
	of frames, one sample from each channel per frame, in the order
	of the channels in the list. 
	'''
	while True:
		try:
			yield b"".join([next(channel) for channel in channels])
		except StopIteration:
			return

        
def wav_samples(channels, sample_width=SAMPLE_WIDTH, raw_samples=False):
	if isinstance( channels, Iterable ):
		# if passed one generator, we have one channel
		channels = (channels,)
	if not raw_samples:
		# we have audio waveforms, so sample/pack them first
		channels = sample_all(channels, width=sample_width)
	return interleave(channels)

def buffer(stream, buffer_size=BUFFER_SIZE):
	'''
	Buffer the generator into byte strings of buffer_size samples

	Return a generator that outputs reasonably sized byte strings
	containing buffer_size samples from the generator stream. 

	This allows us to outputing big chunks of the audio stream to 
	disk at once for faster writes.
	'''
	try:
		i = iter(stream)
	except StopIteration:
		return

	return iter(lambda: b"".join(itertools.islice(i, buffer_size)), "")


        
#! def play(channels, blocking=True, raw_samples=False):
def play(channels, raw_samples=False):
	'''
	Play the contents of the generator using PyAudio

	Play to the system soundcard using PyAudio. PyAudio, an otherwise optional
	depenency, must be installed for this feature to work. 
	'''

	#! channel_count = 1 if isinstance( channels, Iterable ) else len( channels )
	channel_count = 1
	wavgen = wav_samples(channels, raw_samples=raw_samples)
	p = pyaudio.PyAudio()
	stream = p.open(
		format=p.get_format_from_width(SAMPLE_WIDTH),
		channels=channel_count,
		rate=FRAME_RATE,
		output=True,
		#! stream_callback=_pyaudio_callback(wavgen) if not blocking else None
		stream_callback=None
	)
	try:
		for chunk in buffer(wavgen, 1024):
			if len( chunk ) == 0:
				break

			stream.write(chunk)
	except Exception:
		raise
	finally:
		if not stream.is_stopped():
			stream.stop_stream()
		try:
			stream.close()
		except Exception:
			pass



def file_is_seekable(f):
	'''
	Returns True if file `f` is seekable, and False if not
	
	Useful to determine, for example, if `f` is STDOUT to 
	a pipe.
	'''
	try:
		f.tell()
	except IOError as e:
		if e.errno == errno.ESPIPE:
			return False
		else:
			raise
	return True

class NonSeekableFileProxy(object):
	def __init__(self, file_instance):
		'''Proxy to protect seek and tell methods of non-seekable file objects'''
		self.f = file_instance
	def __getattr__(self, attr):
		def dummy(*args):
			return 0
		if attr in ('seek', 'tell'):
			return dummy
		else:
			return getattr(self.f, attr)

def wave_module_patched():
	'''True if wave module can write data size of 0xFFFFFFFF, False otherwise.'''
	f = StringIO()
	w = wave.open(f, "wb")
	w.setparams((1, 2, 44100, 0, "NONE", "no compression"))
	patched = True
	try:
		w.setnframes((0xFFFFFFFF - 36) / w.getnchannels() / w.getsampwidth())
		w._ensure_header_written(0)
	except struct.error:
		patched = False
		w.setnframes((0x7FFFFFFF - 36) / w.getnchannels() / w.getsampwidth())
		w._ensure_header_written(0)
	return patched


def write_wav(f, channels, sample_width=SAMPLE_WIDTH, raw_samples=False, seekable=None):
	stream = wav_samples(channels, sample_width, raw_samples)
	channel_count = 1 if isinstance( channels, Iterable ) else len(channels)

	output_seekable = file_is_seekable(f) if seekable is None else seekable

	if not output_seekable:
		# protect the non-seekable file, since Wave_write will call tell
		f = NonSeekableFileProxy(f)

	w = wave.open(f)
	w.setparams((
		channel_count, 
		sample_width, 
		FRAME_RATE, 
		0, # setting zero frames, should update automatically as more frames written
		COMPRESSION_TYPE, 
		COMPRESSION_NAME
		))

	if not output_seekable:
		if wave_module_patched():
			# set nframes to make wave module write data size of 0xFFFFFFF
			w.setnframes((0xFFFFFFFF - 36) / w.getnchannels() / w.getsampwidth())
		else:
			w.setnframes((0x7FFFFFFF - 36) / w.getnchannels() / w.getsampwidth())
	
	for chunk in buffer(stream):
		if len( chunk ) == 0:
			break

		if output_seekable:
			w.writeframes(chunk)
		else:
			# tell wave module not to update nframes header field 
			# if output stream not seekable, e.g. STDOUT to a pipe
			w.writeframesraw(chunk)
	w.close()






if __name__ == '__main__':
    b1 = beep(440, seconds=.1)
    b2 = beep(880, seconds=.1)
    # bb = list(b)
    #play(b2)

    def dot():
        return beep(880, seconds=0.1)
    def dash():
        return beep(880, seconds=0.3)

    def pause():
        return silence(.1)
    def pause_letter():
        return silence(.3)

    def s():
        return itertools.chain(dot(), pause(), dot(), pause(), dot())
    def o():
        return itertools.chain(dash(), pause(), dash(), pause(), dash())
    sos = itertools.chain(s(), pause_letter(), o(), pause_letter(),s())
    sss = list(sos)

    play(sss)
    #with open("output.wav", "wb") as f:
    #    write_wav(f, sss)



