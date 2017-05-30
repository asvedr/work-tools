# -*- coding: utf-8 -*-
import sys
import re
import argparse
import os
from argparse import RawTextHelpFormatter
import xml.etree.ElementTree as ET # for reading API.xml
import bz2
import binascii
from functools import reduce

class GlobalConf(object):
	"""docstring for GlobalConf"""
	def __init__(self):
		self.allow_warn = True
		self.allow_replace = True	
global_conf = GlobalConf()

# hexlify(bz2(events dict))
default_coral  = None
# hexlify(bz2([events dict, dataPools dict]))
default_hmi    = None
# hexlify(bz2(events dict))
default_vs_can = None

vs_can_path = 'nv_navigation_gmock_unit_test_services/source_mirror/vs__vehicle/inc/vs/can/protocol.h'
coral_path  = 'CORAL_CELL/inc/CoralCell.h'
hmi_path    = 'ui_vp4__hmi_mdl_exp/guide_api/API.xml'

path_from_workspace = lambda ws_dir, path: os.path.join(ws_dir, reduce(os.path.join, path.split('/')))

# One process-thread-message filter
class Filter(object):
	# make new filter or return none if string empty
	@classmethod
	def make(cls, param):
		param = param.strip()
		if len(param) == 0:
			return None
		else:
			# parse conf as dict. add brackets and replace keywords to env
			try:
				env     = {'proc': 'proc', 'thread': 'thread', 'message': 'message', 'func': 'func'}
				try:
					conf = eval(param, env)
				except:
					conf = eval('{%s}' % param, env)
				proc    = re.compile(conf['proc']) if 'proc' in conf else None
				thread  = re.compile(conf['thread']) if 'thread' in conf else None
				message = re.compile(conf['message']) if 'message' in conf else None
				func    = re.compile(conf['func']) if 'func' in conf else None
			except:
				raise Exception('bad filter schema', param)
			return cls(proc, thread, message, func)
	def __init__(self, proc, thread, message, func):
		self.proc_re = proc
		self.thread_re = thread
		self.message_re = message
		self.func_re = func
	def match(self, indexer, logline):
		'''(indexer, logline)
			indexer - schema to get proc, thread and mess from logline
			then matching components
		'''
		if self.proc_re and not self.proc_re.match(logline[indexer.proc]):
			return False
		if self.thread_re and not self.thread_re.match(logline[indexer.thread]):
			return False
		if self.func_re and not self.func_re.match(logline[indexer.func]):
			return False
		if self.message_re and not self.message_re.match(logline[indexer.message]):
			return False
		return True


class Indexer(object):
	''' schema to split log line and indexing in it '''
	@classmethod
	def try_lines(cls, lines):
		# default:
		#  0 |  1  |    2    |  3| 4 |    5    |       6   |     7       | 8  | 9 
		# day/month/year+time/PID/TID/proc name/thread name/function name/line/=message 
		# udp:
		#  0 | 1|2                | 3| 4               | 5 | 6 | 7   | 8   | 9 | 10 | 11
		# [my/mm/md mh:mm:ms.n] 04/20/2017 17:15:52.677/pid/tid/pname/tname/fun/line/=message 
		defcount = 0
		updcount = 0
		num_match   = re.compile('\d+')
		brnum_match = re.compile('\[\d+')
		for line in lines:
			split = line.split('/')
			if len(split) < 10:
				continue
			if num_match.match(split[0]) and num_match.match(split[1]) and num_match.match(split[2]):
				defcount += 1
			elif brnum_match.match(split[0]) and num_match.match(split[1]) and num_match.match(split[2]):
				updcount += 1
		# if most of lines is in one template then all conf in this template
		if float(defcount) / len(lines) > 3.0 / 5:
			return cls(9, 5, 6, 7, 9)
		elif float(updcount) / len(lines) > 3.0 / 5:
			return cls(11, 7, 8, 9, 11)
		else:
		# if none - we can't parse this
			raise Exception("can't parse this log", '%s %s %s' % (defcount, updcount, len(lines)))
	def __init__(self, maxsplit, proc, thread, func, message):
		# All that fields is int indexes
		self.proc       = proc
		self.thread     = thread
		self.message    = message
		self.func       = func
		self.maxsplit   = maxsplit

class Mask:
	'''
		Container for rules 
		Howto: init, set 'indexer', use save_this_line
	'''
	def __init__(self, filepath, is_exclude=False):
		try:
			with open(filepath, 'rt') as header:
				self.matchers = list(filter(None, map(Filter.make, header)))
		except FileNotFoundError:
			raise Exception("can't open rule file")
		self.is_exclude = is_exclude
		self.indexer = None
	def save_this_line(self, line):
		indexer = self.indexer
		assert(indexer)
		if self.is_exclude:
			for m in self.matchers:
				if m.match(indexer, line):
					return False
			return True
		else:
			for m in self.matchers:
				if m.match(indexer, line):
					return True
			return False

# codes for VS_CAN
class CodeMessageReplacer:
	def __init__(self, wspace_path):#schema_path):
		# read "protocol.h" and make code-message pairs
		self.events_dec = {}
		self.events_out = set()
		self.getid = re.compile(' \d+')
		self.getidhex = re.compile(' 0[xX][0-9a-fA-F]+')
		# if schema_path is None:
		if wspace_path is None:
			self.events_dec = eval(bz2.decompress(binascii.unhexlify(default_vs_can)))
			print("vs_can replacer used default %s" % len(self.events_dec))
		else:
			splitter = re.compile('\/\/|\/|,')
			schema_path = path_from_workspace(wspace_path, vs_can_path)
			try:
				with open(schema_path, 'rt') as header:
					for line in header:
						if 'VS_CAN_' in line:
							try:
								split_line = splitter.split(line.strip().replace(' ', ''))
								# events_hex[split_line[3]] = split_line[0]
								self.events_dec[int(split_line[2])] = split_line[0]
							except IndexError:
								pass
				print('vs_can replacer load %s' % schema_path)
			except FileNotFoundError:
				self.events_dec = eval(bz2.decompress(binascii.unhexlify(default_vs_can)))
				print("vs_can replacer used default")
	def replace_in_line(self, indexer, line):
		'''
			replace one code in one line
			line is a list of string in which message will be used
		'''
		if len(self.events_dec) == 0:
			return
		try:
			message = line[indexer.message]
			if line[indexer.proc] == 'vs_can' and line[indexer.func] == 'OnRecvIPCMessageCB':
				# try replace code to message
				keyPhrase = "Published Event:"
				if keyPhrase in message:
					byKeyword = message.split(keyPhrase)
					try:
						if byKeyword[1][0] == '[':
							# already replaced
							return True
						is_hex = False
						try:
							id = int(self.getidhex.match(byKeyword[1]).group(), 0)
							is_hex = True
						except:
							id = int(self.getid.match(byKeyword[1]).group())
						if not (id in self.events_dec):
							if id in self.events_out:
								return
							self.events_out.add(id)
						id = '[%s(%s)]' % (self.events_dec[id], id)
						line[indexer.message] = '%s%s%s' % (
								byKeyword[0],
								keyPhrase,
								(self.getidhex if is_hex else self.getid).sub(id, byKeyword[1], count=1)
								#re.sub(' \d+', id, byKeyword[1], count=1)
							)
					except Exception as e:
						if global_conf.allow_warn:
							print('warning: replacing code in message(vs_can) "%s" has error: %s' % (message, e))
					return True
		except IndexError:
			pass

class CoralCellReplacer:
	def __init__(self, wspace_path):
		self.events = {}
		self.events_out = set()
		if wspace_path is None:
			self.events = eval(bz2.decompress(binascii.unhexlify(default_coral)))
			print('coral raplacer used default')
		else:
			define   = re.compile("[ \t]*#define")
			redefine = re.compile("[A-Z_]+")
			splitter = re.compile('( |\t)+')
			schema_path = path_from_workspace(wspace_path, coral_path)
			try:
				with open(schema_path, 'rt') as header:
					for line in header:
						if define.match(line):
							schema = splitter.split(line.strip())
							if len(schema) < 5:
								continue
							num = ' '.join(schema[4:])
							if redefine.match(num):
								continue
							if num[0] == '(':
								num = eval(num)
							elif num[-1] == 'U' or num[-1] == 'u':
								num = int(num[:-1], 0)
							self.events[num] = schema[2]
				print('coral replacer load from %s' % schema_path)
			except FileNotFoundError:
				self.events = eval(bz2.decompress(binascii.unhexlify(default_coral)))
				print('coral raplacer used default')
	def replace_in_line(self, indexer, line):
		if len(self.events) == 0:
			return
		try:
			message = line[indexer.message]
			if line[indexer.proc] == 'CORAL_CELL' and line[indexer.func] == 'eCellCtrlEvent':
				keyPhrase = 'M_Type = '
				byKeyword = message.split(keyPhrase)
				try:
					if byKeyword[1][0] == '[':
						return True
					key = int(byKeyword[1].strip(), 0)
					if not (key in self.events):
						if key in self.events_out:
							return
						self.events_out.add(key)
					desc = self.events[key]
					line[indexer.message] = '%s%s[%s(%s)]' % (
							byKeyword[0],
							keyPhrase,
							desc,
							byKeyword[1].strip()
						)
				except IndexError:
					pass
				except Exception as e:
					if global_conf.allow_warn:
						print('warning: replacing code in message(coral) "%s" has error: %s' % (message, e))
				return True
		except IndexError:
			return

class HmiEvtCodeMessageReplacer:
	def __init__(self, wspace_path):
		self.events = {}
		self.dataPool = {}
		self.events_out = set()
		self.pools_out = set()
		self.getid = re.compile('\d+')
		splitter = re.compile('\/\/|\/|,')
		# uncomment this to allow default value
		def use_default():
			# data = eval(bz2.decompress(binascii.unhexlify(default_hmi)))
			# self.events = data[0]
			# self.dataPool = data[1]
			pass
		if wspace_path is None:
			use_default()
		else:
			try:
				schema_path = path_from_workspace(wspace_path, hmi_path)
				tree = ET.parse(schema_path)
				root = tree.getroot()
				#getting all the HMI events
				for event in root.iter('event'):
					evtId = int(event.attrib['eventID'], 0)
					self.events[evtId] = event.attrib['name']
				#print(self.events)
	
				#getting all data pool messages
				id_in  = 0
				id_out = 0
				for datapool in root.iter('datapool'):
					for data in datapool:
						if 'dpID' in data.attrib:
							dpId = int(data.attrib['dpID'], 0)
							self.dataPool[dpId] = data.attrib['name']
							id_in += 1
						else:
							id_out += 1
				if id_in == 0 and id_out > 0:
					print('warning: in API.xml datapool properties has no dpID')
				elif id_in > 0 and id_out > 0:
					print('warning: in API.xml datapool contain property without dpID')
			except FileNotFoundError:
				use_default()
				print("warning: '%s' not read" % schema_path)

	def replace_in_line(self, indexer, line):
		if len(self.events) == 0:
			return
		try:
			message = line[indexer.message]
			if line[indexer.proc] == 'GtfStatic' and line[indexer.func] == 'ProcessEvent' \
			or line[indexer.proc] == 'GtfStatic' and line[indexer.func] == 'Event_SAL_To_HMICallBack':
				keyPhrase = "MsgId:"
				if keyPhrase in message:
					byKeyword = message.split(keyPhrase)
					try:
						if byKeyword[1][0] == '[':
							# already replaced
							return True
						id_field = byKeyword[1]
						id = id_field.split('[')[0] # the only line difference with elif
						id = int(self.getid.match(id).group())
						if not (id in self.events):
							if id in self.events_out:
								return
							self.events_out.add(id)
						id = '[%s(%s)]' % (self.events[id], id)
						line[indexer.message] = '%s%s%s' % (
								byKeyword[0],
								keyPhrase,
								re.sub('\d+', id, byKeyword[1], count=1)
							)
					except Exception as e:
						pass
					return True
			elif line[indexer.proc] == 'GtfStatic' and line[indexer.func] == 'DP_SAL_To_HMICallBack':
				keyPhrase = "dpID="
				if keyPhrase in message and len(self.dataPool) > 0:
					byKeyword = message.split(keyPhrase)
					try:
						if byKeyword[1][0] == '[':
							# already replaced
							return True
						id = int(self.getid.match(byKeyword[1]).group())
						if not (id in self.dataPool):
							if id in self.pools_out:
								return
							self.pools_out.add(id)
						id = '[%s(%s)]' % (self.dataPool[id], id)
						line[indexer.message] = '%s%s%s' % (
								byKeyword[0],
								keyPhrase,
								re.sub('\d+', id, byKeyword[1], count=1)
							)
					except Exception as e:
						if global_conf.allow_warn:
							print('warning: replacing code in message(hmi) "%s" has error: %s' % (message, e))
					return True

		except IndexError:
			pass

class Liners(object):
	def __init__(self, path):
		header = None
		encs = ['utf-8', 'cp1251']
		for enc in encs:
			try:
				header = open(path, 'r', encoding=enc)
				lines = header.readlines()
				# ok
				self.header = header
				self.lines = lines
				return
			except:
				if not (header is None):
					header.close()
					header = None
		raise Exception("can't open with %s" % encs)
	def __enter__(self):
		return list(self.lines)
	def __exit__(self, *args):
		return self.header.__exit__(*args)

def remake_log(codereplacers, input_path, output_path, mask, pure):
	''' filter contents and replace code to message '''
	output = open(output_path, 'wt', encoding='utf-8') if output_path else sys.stdout

	#only for events from vs_can
	#with open(input_path, 'r', encoding='cp1251') as header:
	with Liners(input_path) as lines:
		#lines = list(header.readlines())
		indexer = Indexer.try_lines(lines[1:16])
		if mask:
			mask.indexer = indexer
		getid = re.compile(' \d+')
		for line in lines:
			log = line.split('/', maxsplit=indexer.maxsplit)
			if len(log) == indexer.maxsplit + 1:
				for replacer in codereplacers:
					if replacer.replace_in_line(indexer, log):
						break
				#vs_codereplacer.replace_in_line(indexer, log)
				#hmi_codereplacer.replace_in_line(indexer, log)
				if mask and not mask.save_this_line(log):
					continue
			elif pure:
				# ignore unparsed line
				continue
			else:
				# save unparsed line
				pass
			resline = '/'.join(log)
			output.write(resline)
			if len(resline) == 0 or resline[-1] != '\n':
				output.write('\n')

	output.flush()
	# output will automaticly closed correctly
	
helptext = '''
	Filters log and replaces message codes to their description.
	
	Protocol file is a file that contains enums for vs_can events. Default is protocol.h.
	If you don't have any then code won't be replaced.
	You can find the file in vs__vehicle\inc\vs\can\ directory in your repository workspace.
	* Replacement example:
	  OnRecvIPCMessageCB/1100/=Received Frame: 575, Published Event: 298, Value: 2, DataLength: 4
	  OnRecvIPCMessageCB/1100/=Received Frame: 575, Published Event:[VS_CAN_GENERAL_LONGITUDINAL_ACCELERATION_EVENT(298)], Value: 2, DataLength: 4

	API file is .xml file which describes available events in HMI-SAL interaction. default is API.xml.
	If you don't have any then code won't be replaced.
	You can find the file in ui_vp4__hmi_mdl_exp\guide_api\ directory in your repository workspace.
	* Replacement example:
	1. ProcessEvent/463/=MsgId:20[0x14] GrpId:204800[0x32000] Cnt:0
	   ProcessEvent/463/=MsgId:[Ic_SWUpdate_WhatsNewDetails(20)][0x14] GrpId:204800[0x32000] Cnt:0

	2. DP_SAL_To_HMICallBack/1563/=dpID=30107, paramType=12, length=9,
	   DP_SAL_To_HMICallBack/1563/=dpID=[Engineering_SysStatus_SupplyVoltageLocal_E_STR(30107)], paramType=12, length=9,

	3. Event_SAL_To_HMICallBack/1408/=MsgId:232[0xe8] GrpId:100000[0x186a0] Cnt:0 salProtocol:-1
	   Event_SAL_To_HMICallBack/1408/=MsgId:[Evt_DTV_Preset(232)][0xe8] GrpId:100000[0x186a0] Cnt:0 salProtocol:-1 

	Use --include or --exclude to set filter.
	It's a file where every line is rule for lines in log.
	Rule example: "proc: 'rs_radio', message: '.*State.*'".
		This rule will match process by process regex and message by message regex.
		Match OK if all keys OK.
		Keys: proc - process name, thread - thread name, message, func - function name.
	If you use --include key then script save only lines which match with any rule.
	If you use --exclude key then script save remove all lines which match with any rule.
'''

def main():
	parser = argparse.ArgumentParser(description=helptext, formatter_class=RawTextHelpFormatter)
	selfdir = os.path.dirname(__file__)
	# parser.add_argument('-p', help='VS_CAN protocol file(.h)', default=None)#os.path.join(selfdir,'protocol.h'))
	parser.add_argument('--api', help='HMI API file(.xml)', default=None)#os.path.join(selfdir,'API.xml'))
	# parser.add_argument('--coral', help='coral api file(.h)', default=None)#os.path.join(selfdir,'CoralCell.h'))
	parser.add_argument('-w', help='workspace path', default=None)
	parser.add_argument('--include', help='use conf as whitelist', default=None)
	parser.add_argument('--exclude', help='use conf as blacklist', default=None)
	parser.add_argument('-o', help='out file', default=None)
	parser.add_argument('--pure', help='exclude unparse lines', action='store_true')
	parser.add_argument('--nowarn', help='turn off warning on replace messages', action='store_true')
	parser.add_argument('--noreplace', help='don\'t use code replacer', action='store_true')
	parser.add_argument('log', help='log file')
	args = vars(parser.parse_args())
	global_conf.allow_warn = not args['nowarn']
	global_conf.allow_replace = not args['noreplace']
	if args['include'] and args['exclude']:
		print('don\'t use --include and --exclude together')
		exit()
	mask = None
	if args['include']:
		mask = Mask(args['include'], is_exclude=False)
	elif args['exclude']:
		mask = Mask(args['exclude'], is_exclude=True)
	
	if global_conf.allow_replace:
		replacers = [
				CodeMessageReplacer(args['w']),
				HmiEvtCodeMessageReplacer(args['w']),
				CoralCellReplacer(args['w'])
			]
	else:
		replacers = []
	remake_log(replacers, args['log'], args['o'], mask, args['pure'])

default_coral  = b'425a6839314159265359a3a16a4200187d1f8050867ff03ffffff0bf0380ca600e5eef796f73ebc80a0b1837bbe9d4848a092a8010aa927a1ad2b7d81f7c44c829ea79a694000d00001a7a801a69a09a12a46403200000000d3114d50000034f53c9a83400d1ea049a889a35089a20d000000000daa1a2800001a000d000012210213468453d440001ea01a0401016042424e1210bc4d25157f53273f3cd89ccfcec49a3964c52b02c64410a58434aca44f6ab0b5c15512d396354e0e2b15f5df09732ad5553e1ad99dd9f7eee5b1598aacd149c75e5cf440cc3684a76c20443cc1cedba91251447d92e2beb0f254049d03689fbfda4bcebeed2c2e4f0bc0da0891049270a7796a2a895f755bbc29deeecebb585bce1d270d46b2bd3d99a1416ddcb923322204475765f78bda48124924820973df8bd75f9dd46af82abe470a3cdcf6c2960eccd116769109f0418be472129c922a1a7bf341295043ca5abd1888d695b876129f572abd2c180603e2fb9998060230802f5fee0c5492a61173eaa7e7779198959436302883cca59626e9dd89608c6af7d51ac3f59f4de76859bd9b0e00a1da65edf85d1b71b1e34d778eef206e64cca90e201dc164966f6c303c22e3af161813190aadb9cf57b79cd569b1dddb77500f280128a933ad5cedacfe778e5942570cebbc37b66840646d13dbaf4b099123419e7886b0b300cd32b841d157e1ae2b17989bb9b6dc3554c5611638a6dbb1ae53eca2e1c19b0d786b837323be3a253b79a704161662fc1771e1e3300cce1ea851e442838977c122de10d93b2b4836f463a180ba475ce051aa9d50e08b72f321e5c208ad980741b7d7c8c1af269b8079b8e8c12a53741be4ca01eb4f26841e49e4910aeb5d123640b077cc31c1ca9ae6f94a8bb894f0b9e0212abd9d5ef4d0e1edab92169c710a1804beaa11cd0048614266f4f01a1d43877ac80a1dc91d858247d3d5aaef3ccf01285f9d812a493e3ee982153197a99f1c008b0e3925b64134a4e850976ad882c2da48de9df5de6c66ec6b0eb9ad582798c1236d54a2da7443b2ec05d5b152ee64922c15474465b56f71364338b9088e5c4b8b24a25273e08e793286cb0d8206c34d143bcbdc4e47583c71893ac3d490b3883041316a11a8e2c6116491386ed162d64b06020197be3389db1a0c4d45ddb579ddcc02a49312984827b66ae9580a2d8756b2110c86372119db2f7531ac9878a8161ead734428881c699862e2443a83039198d84d30783c2891031503ce44bc4f0700122c589ba15a160b3b8dc34d842a013d26dea8e54654d68ab8a8d5642cc92f474a5864c137b957675977a9b354f7911b831870acc3b1bd6b5bccb01ddc4ccdd92621d12946e8366ddde209277bded3bd6a20bb300d6e11dac721e64a4c5a9716b16600bbbd627085b847375c632b6a3933029d3811a35c4787c30f018ac48041005f1108001c903f0fda0fec7ee6632bef3fc85ef47d91ade548263405a0d25aa285270aa411191c4282230187251a2d0113bd529de99081fca4c53aeae173aedbdf8bf03761f2c52c4da3c4101a168520aa71a1b4a4bebb61250b846046775f6a2bd2ff13caa924256e726e830aaa2ac164c0ca6510c91e9617eee620118600051c8ae19ac20ac24202d4194c1db6393c164a893d62a89138a95baa4449d7e1c6361a806c63232984b25092080904a2292613af480a6e2630d40316ec4d078222685f00d052014b50bc7cf70d06fb98a66735236ef37a082a5b5016384840394d1c5155432606dbb134f6fb6235e4f763d76516c47e396ed13796d646166117dd88271f3e257810fe0014301b48584832106280801182c815dc3db31cfa493f9109488712be03d95577cf7b3d122ce1443df4f304ea1d1d41e42dc902408210c60615b1204094a024311dfb76296c002d219eb63b8a06357a4f5b207c0082c8e148af85433d460e1d90678470fe7c237e11cca2578808855251d057ea30962828988c2935aed052caf5371681f6808350169372fc5f6b4968306ab9d58b2ca1aed95c8b5c2f8649d7cc213431721b6c292616ced40b0211b7d0f109898c3e98ec24860e2c0eb9c136f3646ec2aca3a03d35c7acc306178e2b4dd85e06b6218b7f489a0eec46fbde12101e8b916a8218c42380e0b2f6c4c686c838b6983c5a6f84c051495b6678ca2f16e2431cfafbea071159141b206b53a92e013bf433c1c4f6145e50a0e892b4a8ee72ce4ef61ac838d938c63d18926204db867b342f4e9793017e84a95915eef00a8d5fb3aa165a0d3086d888c091182311440050848200c87595ce1c434a32a2eaea7193798341a1a0021a4290576480a1a912284128a34801692536c025c500940c09808490f948d42e64faa41bb321b1188508723f0889c2d884c0dad2852062fb255392572c457903602e318ce99864a640c8bf8ad8c7787705e4d4a54c535b18f376a56eff0f571ac1dc527918e24807a682469d909591d64579056131bc00c061c50641a12bf5921025f20f5934c8464dc80d26a624254bbdcb75bf760224c31cc8a545a491433cb9a1c0c7f05bd2e4691b1ec02bdfcbf6d20943fba0b92802011098540d02526f2701f091c20b66e2e0c59e5be7fbe84224605ade0a42d449cad90ba596d4f3a68536f7eb22f2a502847b3b85fdaaa1d1193507c0012e100be218e0411862b98505c0789837433c06b9d54cd78653e652f30a11aaa5181431515852284292345543d218216511ab654ec76b934243486902139994ae555f68639618735118c7af6b700c27ca4e10db076e7090964e0340d1744d6e80028a10a945542ea8baa914d1556d073dac989e547d4c48df9f288dab94886241da16c3497d6d794374d62a289a62bba8b3af386ee790554a3dbecb84ebb54f225187e208a4598677c922388cd520e331ab6d2996ec48f4318ca3303248951e26298bf1f9abf8da277a3cfa87dacee778efcbc0dc203d3a069296342d063a6f3d61bc450594562ea79ec13949b3dec16e76303b46a240c4d5e30a31ab64cc848c0a8f0211916eb174ef0431b1703205d23720c944a3227db6efd14b30150d5e8c00351b8c0467a0bccb5b1243c3db10ddf26ef532bb6d858776e1a88d0980566b1e21104a9db6da2eb6fa0e01936f5d7aede0ecc2406059874db10611825293e084185f40d0b861efcf4bb1d2365f7787004fc223a9c8c7d4a5b807bb90079f0a55254d2dc3bef7841823bb989184875e4596980c535092969104baa04098d66f0c5620b1491159108db6f9478fbe4cb237f05ad6af31f7033b1525a66f240e9d83ba689ed51b4e28406601210e90318f6090501e54df47aa2c30c60d2986dda75ab51eecb5954de4782ac139cefe8b48572bb141404405e486c8120b2227736081152c63061ef90000cea2940481623ab3b84f2ef5d2a7002a77d693acc3ec41078ce69846c47d46983556745b8edf259d321171e7c43d5861e339c5354499914488a49db1ce287c6f469c51730c9620e5080dcd47210d2811830d620e9128448882facef646db64d90b32b1356a9274fa49ef6b4da369eadf36cbb424c4237bb7e4df69783aea7df107c19b7cd4f6b9184cf13db9304f699aea7281e8bfaf9f1b6710d55247398a1381433b6e18b292c002cd45d76903cf1ce8df2191b470ce6b5beb0ae0df32b52c77becd7c6541da2e0cd6880f09dcda14447d5a249998679a0edc501e63935b60b39e05d634c1e62f329dfccd8d84f20c7894c76b792ede5cb715316b3bab3c35640a0902b4c40ce5ccc7aa73ec287be3e3af3889247ee1fc5dc914e142428e85a9080'
default_hmi    = b'425a6839314159265359a88bcde70071395f80600040847ff03ffffffabffffffa607d7ee3a0f15ecb6c7b8671f581ebe00e147cb22a8d1b6607b0d38a80d14d6079eef5d3c00638ca01bd600bbee0076c72f902d7d39d6d8188c81b2c9000000000000003ef7d8cc8f8b873eddd6b6b1565a8db5797275926ccf738e358f2acbb0eb76271b74ca874c45dab6ae5920ed016373b8e134de587820d7b625ea9557b34d3401a742a986df4fbc7d1c280507cd6281d397631dede8eeef55e9d56936f6d213db4addc74adda55e06b7b675f77df6ee61ae78d7a7504974fad39bd87a7a11d32540529eee79e6cc7b6e3af54e4a7a6a6cecddec7100e8a1d7aef47b8c145f7b9d2a568d3ecd05cdf50eaf47d3d52ae9b4f6cadad9d829bee75b568f462a55db49b2b42534204080100084c226d10140d0680113d1a699529494f246868d34346406832311a64068031a44848246921ea03d4d3401a03d4340000094fd524412226d54f4f54fd3d29e54ff529a6d4c83d040c80000224880408000991a984c4c8a9fa99a141a69a008910880434109846a3d232a7e6951b500f29ea00198e2e5136aab5b8aa0a09a0a0a09fefedde4f2f87f166bd46889e45017f7d8725507f882553ea24b717f647d45cd3939bfed72112de87d4d8b2b7272452aeb336347e508ff0e2e964924521fe4367c72d5a84bdc164d88abda73fc76d911efc199f872b8d6dd0fd511ff58af7cf90b1e0c7bb26570caa44ac919ca492b5fe91ae9f4646a45ff718b270f88ecc4496663897ae85cc53629ccc05bdcc59bc6b17ee7c6359c191322422072a3fdf3df54ff2fb4cf63fe4f7e977d73fe399ede7b4411443914203211b8044a42108e03211b8044a42109109469418a36034402037f312b5a914814644c4a6465465914559c675cba97023230c8121932246b692630b1b32bae56b0732ddd16e04b0a0218390845014a02901464688d92d89444d1bbbc6aad6ca8e7d16e70ba6b8d58812033c2b2677be93a44e7ff3b1c637dde2ff15df52729ffc671d779c113ed62fb626551d7f51660a64553a028588908215942399852279540b72e3ebdbef9f405d4b17c233edf95a7e9fa72a16e2ad18c7fedc92dd23ff3b14cbba9192e6fdd36edef8e34cd6effbaff7e6e75eb7d4b5b8c8cc6a6d7f17d59c6b7fd9cd9cb3f7f39cb6d0d135f3135c8f747273ecaaaed4272338c97096043f2ef46779532f2e9c424244a484850850edbfdfb77ee8edd79e979e95777f88512bbfff4534e2f9f8be9ebe56ccdfa420b4fc79721baa35eff1ffdeefb63e7ef6df575ee45b908dd14c3ef8d7b90e975770730cb92e790ed993cba13c88847aa8a91bc4b900bde9a85446a14c782dde6b9b05c172a8b0ae99666e33133172596d9632e2b9cdb14d526cd5b9956a4c6965a224be9f9f32ebc37e39713c75b74cae7ebdd9df5bbce7aefa18866130658498662126676e2898d14718dd34109096c9c1210ee3a6d78edf214356fa5ee3b096cc0e3a0c9356cae518e28e95c308484929249229129248bd7aba612feeefd4ce182bbc9002550b2565bea35547f3c5915ce27c685122ab4a84ac1a04eb89551d96aa26a92450b60852d55c4a431746305048216b08517435142ea1452c8ab6c5811bc89c51c704d131946422b2c962b10e2623b3b48953d105d5f476887dad69dd7af9ac2e08f92cd58774a8afd528834555c852b354b7947682f13df3339e1fe6a747dca8baa756a43ebca9ed472884baf4ebd45090924221909c7e48dfaeb7594e93b43f5c46357d5f75a77ed149cf37855a98c09c75b90424a13a1243b8e932074f08888826007ae99e7fbf5ccdf8606c2011c33df530b7bda9afcdc7e2f6ac8f65e0a7fd39dd5716d67ac79ffd26d77ab8dfcf8ebff3d856fd3b39bba746d8fe150504cfabf2882827ffbdcc1d03e8c9c60fedfdbf4a7b3fccfe6b58ad6b0ab48a52914a3d2948ad6b5aa4924912494924a92dd49249624b345add2aff96f36b77d544e62cde5892c49624b125892c49624b125892c49624b12ca4523896552291c482cddbd7486255619a22b9346b378ab6493bbb9c9624b1258b6c9b26f7773559364de0dddd3bbba492785e6dd1ab1c29248ad2772ece65d9c2492492492492493449249273732b32c5215a12dcc5bba49249249249249249249384924924dd9c276f09ba34852096e2d2732f7330924e126c924926ece12492493984ee1d1594868d3a2d692733094b6db996db6db94cb6cdbb99162e45c8b9cde4bb6db6d0a005b6db6db6816ddbbb931ad9bb9b2edb6db6db6db6db6db6db6d0b6dd33291abb725036db6db680000001437376e47c8b77554a057dcb6972c670afe5479cbc2c5581773af6f5e5d5596ec2d000000002486f7cbb3c896c9bab49c0002480000006dc2c7c8f9bab9c924926424924936eedc89e662c4f649149bbbb763e47bbab7924922003befbef95e19d75d678f1bba0001df5d6d78678ebabc7800203befbef95e19d75d5e3c1676681bb52e47998add00091112e42eed4b91e662d5ea3470e2148569dacaa1cb4d6ab99d396775574833454bbf056f0e74288ccc23fafd55e75eaba3d6c57c2a1da55ea3972ad8f2e2d4d6651645b1e5cd4a49247994c8f62cb9a9e65caf62cb8b646b3332a5b1dba9edb8f63b56a4b6dc523d8add5a5c4476dc76dc523b444579cd34ee6ebe6ebecbaa77755d79921e23309faaaaa796f6d5c76d948ada0576d405722b692b45ab15c6a61bcf50cc387d409cc15430e7ab30e55016efb19fbe8e482c7e44b25a6799b814ccf49371adc1a01a01a01a91a01201a01a01a0244a7a16f5d79bebae75ebc322f22565a6f99ab2dcbe9a4944b704806a034464786191aad4586fbdbe3d7aebe3e3cfa7a8bcb38e73e3d5dd2dcf4937b625ce16322248c8464642322501908c0644782ceaf9cef9ebd79645e4563e4c5873ccdc2e7a4963d9a0911804403211291908c8e423b1ea1f5fcd9cfbefb9c17e82b45104dec3a45d61fbdef56fb545144676cb59aa356ad65f0f3e79f1e3c78f50f3d6e2f23d969cf3370573d36963dd1a2301222210888e08603014844fd4e1ebd7ae75d785afd4565a643a6eb2feaaf7a81ac979bf66666fc82dad597933e23870687d2a2779554061fd99aac2bacfafa6668c3b3eeeb6de2afa26e76cf8eeeef160a16a2eed8f39f3fba03931d5d5ca59242695a5b3a9c920177272dad6b1ed899a68a8b69656239a474e9130db131ed6f6871b727774724924936492f4ae7849d249ed1a7b7a62ae4925ce3ac6db99695b18d0679c97371a9242bc42c161de2611126d4a4846d108da211b442368846d009a01321dfc5e798cfcc975bd0000004440000003adbcea2e64ce0009492392408001966ccd9b2690204225922bbb89a308b32b5660b3312564c9998d2b22b84d5100927d544024e7a87e5fea6d1007ea9dfd83157d7754013af5bb84beefe562f493de5bd8e1234b24c96a6852195a5ef84599b89ac8199892cc264ca92a9a913533071d6d97164591645916466194564e3ade3adb2e994545919859151515164e75bbc75b99c59164591646519514a44d4b212faf2ab173d8bdfbe759befebcf31fa8ce129cdcdcbbb2f0b2330b2328ca2c8ca2a32925b667b2a9ad89291a98b2d526e5986525a5b5d565a9d7292d2ca2b4944922438322df0f3dbdb99edebcef757a8fd4b4eb99b84f65eefc4d771b4a66fff5b40031db408421140213b0723f8f8f8cf8d3e2f9dfc71fa8c9d53adcdc14f77c2c21084ea965b6da584210828414272322cb7d7adf3d2f55f91752c29bde6e13e126b9ea9325a4965a8b2d2cb4b2d2c56882d56882dde5b4055fd0998f8f98aa57699ad3627aabde19dda68924d65b55b6ab6d56db6ab6b4d249292db1352db626a50e03911e5e677e752bee25c37bab3a8489a9d044d402268844d1089a38589ab4b1bb44d12c4d0b175eabccac1a2aac5b668bce67cc5524eeb345815ea5caac8d2a224495b559134048d9089a0b4892b0892211244226a00d49bdf333aefac31f712b37be5e7ad77bf10403d3a596b565ad596b5644234b296365b53569624ad2c69772665cea244f0b89cb7de63ceef59ae362aee84ccc997b5122ae9dd5f672efd3c4ceda76fde02b0cf9753eaad7576e7c9f763469ad5ced69272c9595a3e79a1cdac69c7c68aab5806f6a6b04ce31f271477777576df2dd52ef0ca71aa54badbde1786f5c939092a482f0ed69e9ddcda9d1c924924d92417ab9f1252ddd5ba3779774ef59367ba3736491def9f7b6bfbc4373f6977f22c016ee41fbdebff74bd53ff6c59931a64d351c80268d9776b9797e0afd7007fd4fb7c7aeb8f816da151fe7191117e671e21858b281874621cbe513d7c092489e7e9e7612d6a12aa909134034804c06c04d00d20fcbb97e76d5024911bbbb5ba77cefbeaec02873aeb9d2001245732a6adb535ccccc49db6b4adb5356da92b12888f57613dbdfdbdeb7776bab213bd2f25a5d206b2b5a6009568ada001b4c4124921c1e1df59d9d758fb8901bd67784e402740000005b6db68162577bcc93ae2ecf07755b77bc6b4dbb00002800280a02c7a8b2d6d767678bbf65deb35a6faffb542ac002a868f5098ba53ef4dbf665eb3a6ebaeaeeebc47abedc939377e8eeb40cbd66b4dfeed5d80001e03c08aa371f29de8efc72f59ad22efdee02811546deb4aab853774b2cb35b46d7ac016281caa0f73c9f716aeb32d335a6c7e83b851f560f7a95d50934fa4b7774f2cb35a285cd26eee6e1ca378055e1d9f950c913bcf010a4be4c5707cb30b9f26bbb37e3502bed8fbb0f6d9358964baedde6a8bb44588ad70ef65e1e5249255d0b132724b43bcd952c9ae496df2123d6dd32dab3a6fae44a48fb9b9249239b2492f3b5b9870a5bbdc94eee3276224b4e2aef5c6318c62318c6317be9c9b6f3ef8a51cf7f6aa7f22a3172e9ad5ee1783bf75eac986ce287f780268c78fbed94f7d86f63db77afa6ed7da82db42c3fb466f8e4733972bc3492cc0575b07f01e872dda12cde8cdd778bf7ec661f6dd000006b600066b28cc8cb23322cc8e9f72acad6b5bba001fa72bf7f39d759fafeff5cfb23af1d5e9eb3db2baf1474c9b4c41405b74cdbaaf5d565c8574c80047654db0fde9eaf0f7857a850af5d50ec9db4c31050186569b954c65a0b98e6f7def8df5cf48ebbf578eb5eacc8290c6ce2be0c50178eb4dd3f557aabd5758eeaf56bd62803aeb8dd757bd5ef58a14cb26975b1e178ebb6d5f9dde84ed8a19de95463ba11e40c4142f253dba016dc37a03bbbabc006615fe3924c15055eb2a2a61259b9353ee699de582b75e5dd63b122e5bd4caeecd65dedcc4a73fb585b896ca13a726fe91d5d8174da2743b3d9bc5aa342239785f49a9d3785bbb3aaf7a4eee93bb9b9248db25b6c5a2dcc3852ddd4bba061bd7ce1ea7c37bafa47c9252f6dbd4f0379231f2efe7461a832e3b92497a48de86678663643f73c0d40f942b75955cfb14e5149060f65521554055244d674bb44735cfb3f54a27916a38e581988ba577aab51ee944de9edd2af5557f0e54cba5d320185d114abd583d595dbd9b9695f00776a6dd2f557aeb1a26af5e3a2f5edd757abb8d6b3a69f5f018cd3db62eb33b13b606374475d8a79d9a9e3030ba23aec00f2ceb658075d11582db79493c6286174f6e80dec3f5ee5ddc5ff2276c6de017fdee76e7d315d1de87ce660dbabdded7cbb4cbce504d20c346a0bc3049dd1cdbd3afbf1c9df19f1bd7d786f56fcfe4dd0ba174df13a0647bd2d50a0760cb2efb532cd16f5b18514e2ee953bb9f37246d92db62f1373312451e1125a9a85d3e56c3daf8bdef7bdef7c5f960667606e3d819c5dbb762c76ad87ecf0ce9eecff8fedabee5f45516459164591519522879415491412a4877d6f55ad67379af0efb5f9498d605b758d80050a142850a1543746697bddbc0b15ba6b057342eeaa85d0a15e20e62c50f0a1a769ed5d3af56e5bbc5b0b1437769edd3f541ea46e4c742b841b7dbb55def2bb5947ad75d6de6f5414ebd46f8673ed4efc2f3987eaa36b1ac238215764914abd42ed1c21042aec9a55ea1a2d3dd43879de175e9ab2ee83defdb4852a0174fdba73eb8baae92e746b3ac9b8aeefb5f77417db8e9e69cc74265627c5d4eda4b1483375b67f027f6e09b2fee5d3e4dd839a6cb0f4e68a537648f0107934d0de724d7d4dc82f31b9ab9ceece7ce44db724925e14dcddd3ab4eeeef26baf9df55555695bbe3135bda2d6b62e9fab0d861b766ecdcf9f19a5142da064b2203c8ec23da3221830631d738cf3874952bae2e19de7af0368a4b1862ed1fcd17490b313dd8b8509db2855746ea9b5c45fac7763775c39e326eac537c5cba6259665d2a9c3d75307b057a865d780aaa0866691d32a076760e9e6cbdb7332b28cccaa146ed6e578716465c65aca2cd56b2dca22b2a66ac8b217c21862ba6f2ecb8ae9d7a87186bd449caf55de67aa844d489aa092912501240268d926df3ad5e4e8f33bd8ba4d4efaa9aa09a226a026804d11350134175eabbbf555d9b03375ed7392f3b6501c3f5b3805fd8ff66e50ada012eaf9cafa76f6577773dc37a663bb7b3572967515b2538c115ad4f65466fa5b8970fc6b35cba79b95ba4eafa37d577f5de6b798376de76eb91553db3ad9cd78e634df390593dae76a71f737248db724925e14dcddd3886147b4ebe8b39765f46136497c7874e73fabf6abf2aa87e8df7e869489809201a4024c245223dfcccc690400016f5d75d0000175d75d740892ccc000b6d00092730f5eb9eb8a762f7f2e737b5d361a412448b59558894092000c8029228aa5126daea89b44811c7145236c2d42680495925b6c9224ad13b1db522380d069376f9d77c51dc98777ab6ab6bd5777eabb0000000000280c9269a73ae4da5ed00000000000002e64869f5c666ebf6800040069efdfbf3e3bf5eb9cf7d77df5de7b7329a000000159522d20775dde530c0cbbaab15740002b6dbdf337cec5e700372b6d9e09b4036e1244d48fc069d405def3d7aebcaf5eb77ae73ce7a20000df5a6b9eb776b3273a960000000000655d800d977c6c00ddccd276805bfa1edb17f77ea35b436bf2ed4635fdf47d7ed497e66b77f3ebc3f7cde9eece57f33d4ae0c22f6ef1a80194e9a9d8a19ae5c3b0114137d1c74f84cbc6966eee33bc9cdf68174bbb73ab1c7b37b9d613008e5f774eeeee9236db6db930eae73777710ddd3661c339b85d28ddee72f7de759e4eb9cebcedb6bd6f80000000000000474bebbbd4e708baec36e2ef8200000000009004b96f7cbdea3b3b86dc7d760492000000005dd8a579c71ed5bbacb6eb7cfac0000000000a1d9d8b5aab774af5d6fb8003ebbaa00003ade626f3c2b1d6754a1dfd5760001dd8000000adbdce654eac362b651ae0001003bb7d79ebcfaf169ebd5cf59e0c180ddf3cefc779cdf173c6744048dbc73c75d7875bd78baef3b0000000ebbc42b315f1d03a8d5fdf8d457b79dfa71a5b63aefb656bfabed95bf97d2fe8af373beedfaf37ef355bbd7d9a5da5c53860e6f38ceb557d3318df713c01cc3baa7754bb6fbb376ef3147ba2e61dd2aba47149524abc2b25b99cdb8db6dc9249096db17939cdd2b32fb326a98abbba32c705ad4ece8fdeaac7f21762eeec58b1600a0280a02861d5eba3ae5925331671ec924924924924806ef3559d6f5e2f19d00001def8e73bf1b0df173c67600006cebbebc7771f1e39e3cf978dbb0f5bbbba0927419df5998bb53977be4d164927a0210002492f2dab7a5d75da7c0bcae03c00007d77401b33994b05d9746b7e0aeec0000000000032af1f73ee3d799af46dd689249d4c2db6d2100c5d779bdf77aeb140cef669c0e50000848400011d5aaf3d73b3ae79ce75d234299e6cdb12e7593762f57e6fe2b557531de1de9f577c7cd29529f3ebd31682b5bed36f29fb5ef5ed61362c28d25128b3a06e6a4ff3ec0f77af4b4feef9ba045b6966ef61a993baaf26e5905d3c8e291112295785582a41dd23eee6e46db6f5b72f4ae7849d2493da7b6455cc729c5c997dddcba3aaf7abf27619241f13c376dd9dd14dba3d86e9fd80e6e561d5dce7757c5eb9f73fbe31f38dbee122beae726e9c927467a34ddb9ddcdb61c9249281d5d2773d5dfd275f36dddcabc59ab3e915756471bd9a8d0dc9ca5d2550b8e48b5022fbb7b5f6c8ef25e6bb546b8e19d924e7146e39232226f2508fa9c9f91cfb4fcdd7d5f4a8e49248dbbbd7ce2bb62171f3d9d249246db926f29321d135b78da3b6a6e5db9d2029902ded986ad0b97605520a758154c5c7bbb22e736e771b797bdcb3bb9f0ec6d74cbac583464c12dc21270969babc7b9a4d9d72b9ab6898e6f749333b9c437adce76f033bd70ca5dd6e8c5bcb1bec55291d31d8a74d6a76639635dce1ca2bc287181f7688606fb663e0798e7393e532b52a1c11c92093274ba3321ddac5d2f2f60e2c1a575697a5f7372f286ccecaeac6662b7b9282a58597b64db925d4e5cb20cacb278f80463d71927376b360dcdd4dc91c9249248dc925124487c7b24e35a9cdc39a294917e7d5bc7312ebfbe3f7e640bdbc8f5dbeccc27eafa17d05e609dd1527535d875875f25165c4eadd40a09c10e9dddbd5dc3b877604b9ef4c36de763be91447779f46f64b934bd967b958b9d24c9bb3674a97937a4b8a6566648049d4139aab708bb9caf730e902fb6471855b9ba937ab5290826e4598a0922eac704da4c2ae945c7620272f6f77cef5b159d7b77610ed87a490458b1931c739a994c52e45836cbd4256aea5f595d3efa251bdf8f0ae2647b20971c7a649248a4924e8a3d9654925c47b9d97ca3892926eec904a7f77725df53fbe924937749d724f1e705acb3520a22e29805e13140245249239249248e5492e492291cc06492748e49249239524b926e9a2a96ea92777749d1c0333edf5c1a97dbf4fa9f3a7f7c7eafa0faf44929b01bf9c6d5918debdfbafedfbecfabeefb757d806937096b9c957982bb9bd598b10bd6f229244e5bdd9d9cf1c6285ac73376a4869dce925b924d99b8fab392e9275b91ebc80e648dce92d6379dcd362a6eec324eee751c8c7771b6db73942e4932491ebdddddd6e48efbb502a46dc9248f3a4979b7926f4d5176766b88c91491b6c96ee388c92e280eeae739c9d1ccb92772eeeee9a9249496dae54a7d8e7753386b45efcbee9bafedd5cb9ce900336faa4c2dbe9d24921bdabe3bcb9ca7b8f7b9b8d26db6e4ee924e92e492491b9246e4e81c93a48e39249249249249249249249249249249249249248fbbbbbba39dddddddd2777777774924924922ddddd95200e249249b6ccdddd9249249249249237249249d248dbb553938db9d3b9724971842917433723e7d98927c3b75ecab1bce01dd2666ef7491b6dbe6e4c3236fb7624db9ce2491d524924924124924803924925eef24a713dc722eace4b0ea24825c8e473a438ddb9c374a93a35a9252a052e1c9ae49b9bbbb2491c44e48d3eee71774ac25ae517737249bbbbbb248049f7d3efa5fd743efaf333e3dd6b0b6752eef9bd5ce492491474db6df48db337648a58beee8deaed53a3d25d2268b22d15249bdc75b7254e9d5c03ee6fac9deedb6f5c3137b23d71eb8dc6c89dc5b6e49249318be2318c53196595f2fb6efdc19863e0f9c3a648f1f1fcc0cdf55ff2c3f98bfed1fea7ac11743fa94aa7ea4d7fd71ebeaeae8114ff7083e98bc797f60cff7ebd62ab818a85097593bebf39fe3af5cf23fbde312aed2e336397473964e4afcf470fdffac435130257805fdf7528c92fcbd3fad3f6e14c5138c90931a6dbb7e9434d5cfec641106d56966782a9b824822cb7340c88b226481d0907ee7f5c60a8d4afc39173fc3ffca5abff8243c674426fa2bfc98b6dabc5a3668ebc131c7f3c8e51872e9c7289945a88420335771bf0375e16aa2a69d190ffd63c7ce1fe6cb037e01882f19701cfd3b539f973763c7e2934ef1f3660f867be143a103955048483f089643a10030cf6f5aace317757eb81ba4425f1b5a9efb6576f6479eb778fdfef925e3a26ba275574f02f7bded8a16e14bd04d13ea724e11afb61417dfa4924925dcc87c4711cfec29fe15470110434fb7b628e50edfd0e19f912afe5c9032b6f37f9d81772bd3238aad66cc4bdb45190115445f2e7be74b9d75caaba49898941e31b0ccbc2d4ac464cd267d3679bbfc0b0dbd687e086322c92128b92a1e77a7fa1bbfea4b86149f8c47fb92ec951dab0c79153b40c4cbfd39bdd049649d7baccb4b1fa850ce0e36309e3e93ff383e6770dcda826d8bf71ed9a74c1eb751f61890cc64c7f2ce07c1e6f1db8c6431f2b166e5f7d32333b7a686b1d23d39b8525fa55dbaf16499b2cd1a238fee374a0ce98abc5b8879c1d1e367c69e05a7a687780e70d6d01f4c4d82f83012c886d9a712446d4346ba48f94f6f19477cbeebbc18de88fa87ca8fbabfc11d6491af3f5b9723488d73d649ccddb440ca28bdf6da5aabc977a3d3e98a44e4921aceee9bcdde367c99dc4fd8e8baa739106e47a042913819bb14736d865ad06d7372e42e15ca75b4fd7bd12120bee23ffd86b8645b50ade5256eaf025d31e5653e86f2be04217a4c4eb4a60b082c0fb7dffa3ba5c2b406e281d3ae7bbe7fb271c94d5b4686f918e44dbb9a7b09ae55fa57ad93b3468318ef34bd036758f2e7e7a9c78e03f18aa7f9c7e65ff1eff22d2beff7ba9db351df95a0fb5fa9788484e9463e5481e911c84ae3375e72c683236bd29e141af4fe74eec7887c173cc659068bb05fe7da8ed16b87285a5ff1fe085e629c6081f58a048a89de803d1f7cc6d5f5fb177c77e788938d341be897c842f152192fd3789706e1f4dd36d4caf24980286e97345dac06a7ed4d40ba2a18310120e50ae82d386b9178c500d004e111cdafedcf6bcc057883dbe10f39e581c92c35e57ab0fb8a7e972bccd3b72c70aaa0a8335bd8a944f0ba9107fe08d62c7ebabb7d80e55554490842481da649ee98f9c2fddb8fac1341e5c386ba238c87de472572ce6250c7cdaf1ec4f7a63c4b5be25a137dbedf78f2dee95d593202d6b675afe8beb918e820f79b690f94ccc6fa6ccf9d0ea0e20790c6ecf75a6e71cc28e685d439b3eedacb78f1dac68ccfeb3800f7902fc75c9b6ef15a8aad59673f1ba7256ee479c1c31fc41950bd7c0f8e3cfe15f2609f1981d3c7e00dd4c7fd8ff8b1df9b5ee8b27182289e9b07eaa486c009549081087e4faa6d91abfa28d0aa8737ef039ec000a46a84500a62477d2484e7a90851dcec2dcf0797bf8c445156abefe9f895f771a93ed77cb247a5927e2381f489f581f58a58922a421149148842229122448ac2042291193482049fde1d133bd91b8b102b950dc0712481dda4b97ca84b8254dc15063c082c639c030999e9fbfb79dcc714d172a07eea4dccb9588c162fb7778bf63bae9dd4484ddadcd9b6666eed48497f32add36db0cf5b9f2dfd6e3bef437606c4ca2f5ca3585bc54380c10c0a8c8e322e480f9c2ef6680f3e7995933136877fc4687a5a00b3ddd9bb825c5994296f1a4eb43e8a42a793fec80b218fca1321bd207d04cf10e6cebdcc3893cc6406452078a9285312884e7824e9c00f8837537110363af4014b4555efd24c2fcf043f289bc47d73e1a84f1cb6448498a00cc0fb114c4e43b8aebe9877e355cf8079cda026c6fa0404f0519e1e34e7042637ddcb243f3808d4774ddf0e62ae90924350fe2e9220286f1c0941546a11c265d209cd9a7c7c74f36297d6cffb7f949d6f03d01547a4f361185bf1f8480448b4ff65d34a5a2abf76cd2d30635427fa52c162ad12bf9ebcab7fe7c04f8b1c3a1bcf360c17288fd26cb92853e160d6624a25093af355484595e28ab2276959fb415ab74eb9c94f45c15b3f5d0dbcb15df7b5e44890aceb7d72a77c78ec63edfa7c8fd0f4c827afcd2ec1151b4aa516a002ff36e5a498bfdfa4c9cf3e8d4cd2f249853a99dbe408c8a83d1493929cffafc6f7f8d749d19860114247abc2dfa68676fef9c277214898761bbf0d29b5eefed82e6cd6456bb19570bee7b9834a5777b719fc14cac9718ca51b72cb13b598d940f7ca1d488678980b6243304ab4af3f966a914fac8bbe2723a0e4723019191aaa646468f0ccb373f799cc7ef112efc7b6d7595b988dfb1464be28d9a21e13f73c0a3d3ea4245a3c7ed4287c22bba00edea028079db3b7cbd7fe531fdd10c900169f87c7f5135bd6051ddcf55b841953e30426b8fafbbb57e13e930917c63278c1eb826510a927945312c42154fb9aaf5eef75c7d5c2b1a08b305e15c0a30e438ce99ab8726375fea501c033e1056ff18824626cecd0687a71cad6c5c1ed376ee2393d098450a32133e64fc4762a3d90b48be1dc6426771d91fe2856b46072b50ab6c8cb9c68b61acf1ac2bbd5dade7303922795bf976c9cf5b611daeecd08120a754cfe6ecf4dc532e09d30d9857f55920b4398c686686b3cbde74863b477229f4f0fd2e5058afdfc5fd303550694331c52666a7b172bd96a0c232e52afb5bb9d9dfa299abb491849024244cd5fac5d970be1249252f68bfdb7574c02b022308cafa6e850198a00480040002122c492092424ce95d83b7ddf38f0baf61de4ecec310aef3f9d77f5dbfc8cb3ed5494166326561302a0c18c919148c632108893cbd2b02686b35ecb9b9a41ea449f240d15cbe224aa0d9c6374334db48331456838e424704532a5256919adeefee5eea0992cbb40b6b5614336e766cf87cf52dbb03d6c6b25aa2e904882105e88e9a685800c70225449b0529e6212093c8e7db96afa5557898dbc4a26d215d13f1760a01a08de8740fefecd1bb5af9b74f27bb1c45704300c31ec86061ab90560b3f6f5e57c58299b73408ec0ea8fd639febf8a75ab9d6c3ca6b9f59f199aba79318cf393c0f1bdcec8d868bb96cf6da25ac10f4d757f558b03dec114769ca9494c7802604c86516133215bbf7aaff735f8359e1fab33e4fc31f6c78f4368d7c1651b9aebbefd76d65b998b51d2ddc2745bddde92a1eafaa3069cf3cf066de92affa09b8feaaa3f9a4040fa3004241911091159116402404244047e5041f5e9cfe970a0c40d0d0353fccb31c75d8eee3c07e51d55b95349822076aa19dfae5466ef285a1c2ccd6b88a4dfae0ed495428d8b484f2523edfc79e955bd303f3c36371e8451acba00be19be7d631f00dec00c24a371c8e9f2df9bed1e33143d9a338ef95e254ff90b96b01273f85be73979852d6a6718490244901920a2482245985950c66313119198cc8967cfc73944a32c529586514666464c909594521932ac430eb0eb79833192c9319cdcb31d56e1c05d49094c8d0596e4b8aadaf5ad1ca14f3b1f18b9fe3baddb1eef5a19527dfc661f3e1fe034d3081c28a3cd519048410fad9e66b278e252f87bf1561b8c379e93049548b78865102885045fd722b61ec9d7f7b8db4faa3c17c3017fa87e619042fbe71d070ac1fbda4a28a7bd4febbfb89eefa18e785f0fccacaf02bfaec7841a2663346e10e219d0091b8cf3fc7f9ff55cccdf9c7cbfc6e8a71d34af2e533ed11ba7bfc3f7fc7f5edfeebeeb8f00663dd942483f3521fbe7b5b0f1f4af7698da4fa6d9fe939b02835fbd9402c8456447dafb2145d342e2860c2b1ed0f0537114604d2d2a9b389349ddda0e5100bc0cb7507dca0a866504f1150f289201f6880122a2b51712b0d2c57a60538a43f264e35f38509e477e303e741760b9a20522841639b2825b58013d41438da427d4adf9bf4e1ad90a8151350d2af154c52ac9965d4aed59455b4cb2d59156d301c913af7ed605359503a02e3dcb7f1c2e13a2178d76d685ed975e0f3c7598c56031ce2f824b90e68684785549d84f7d7b0c8da519c2861a0f28d25a964f8b41c2484cea67d6bc854b192dc58246c05508e504bbb68985fca338794e0900c2fef587884ce24c821b576de88334b8eefe3f1f8a8704b961bfccb4e4bc638b979af91edebfad75c2d7afeda121fc40a848541d208f6c08c4024509049032c4ccfee05406f069684d69e6b65542a9781968a48e3653b61ca09e1caf67492498ee29b8364125aaaeb88106eb8500a039445201c23ec3317b9212015eec8d2917f29e1fb68d884f1e9af1f4afb1af2c15805503f9c12b66ed4e3dad80d94033f95b87f730e275d22853aec477c0ba7bebb40d4747d215b39daf91ef82262a4eb63518423183dbda775cee1f9152858108270ab2a4fafd648b2221e7c4729f776965ef2e64ebfb11fa9b1421ac7d7785504d1ffa515ac308372578da3645de620321f97914f389c4de157074009007bb7f73c32ceecdaddcf1437c9068bcf97947efbd9fec94428237548af33a1daf17498204e3e5f5530c084ca89632fe465a8ca99f8b4073d72ab994b0a960ab0a9058a6652c4a8c62465dd248799fb6dfb7ed902407d7994150905de3722d58436a2e483c93025df98b5e021f229e03bef74ed4818cedfd3e295063646cde6261cadf0f59e8c9cab3e486da9fc40de0589859c7bdd051d25e48dc39b2bf8aa86d2ef372663ee124512448b116434bb60f63382a8b9d59881b6289024642048d5a0c238eb448804d3965e1b52fe5483b9332d1d24923577492649ddd264998488f92897cca771a1919df7f8dde5ef2bce1ac8e96298604de38e79bebbc8afabda1d67cc6de827e5fcdfe77bddf54c67f4a659c6d5e37b58e8b765d2fc59811804440cf068e909172eda8928a1b9e886d29ab41c99218a259414fb5f2c56da14dda9a659e7ad5f7960a8248ef8009e0a23202810480224160092b7acfb6afb57e13b6b259d68e1c21448a74b549507c41e2d1fc677b43c8e1f4d75c407edb51f8401c488a24875c1491446491541a82ee8c800e8181f5c6519955f49f10f46814b8c4b844cba293b050bd2627d71bdd1e1b6af0307c84507ec71a4012eb2063ec1fdcf2d672ef37d8872358f54241e570b41f891374c537058a8f2fe294a3f1960dd54bf323db1100b0c000a2cd921b0052b506550151221dfb7288320efa29f10fa500627ea5c92485167cbeb5525955ab698377e6fe58fe53eb1255eee0d647c8c0e84325a32ef4d1443dd73d584f44f95606cfc14154921654d21e9adec994b1741e7bc90a9ab196f9541508b1b0b44c95934af45c50d086288b2ba60f60081e947c45fbc4cbf6f02c03a090909084584229082b022222222499942659222224cc49664424562559330942081fb1d8bebcf7b0e629241e90690cc4ef0d7f4cdf7b2d59e8aa35bb1d60334c6e415fb081c3d85aa0498f92198ee47d591e8d2fcbbf4cde39e871455ca1624a2ac07de8ecc538f7545cca31cbadd4cbc3a47cbc5e82997a2afd7f2492ece872ab4efe1c0db9a49883463d2fc0508c143a8207ac31554847e70fdeb1ccd35c5df14bc57d2281009048b20c828c889242205100c8fa6da599e0815028afec485868714d01e2fdb75db4576c39a587dd0ce3923ddc4b7c35269abd9852f1fac1e99b9febc4fac585c31bbd1b281bcf61dbf445992e980a23a63c41edbd1fa7dc5dccc223f66f3f7f2a4337da610fa6fdb7284dae6e3bbbd20f90e47c3e56a62225d5d9699a2ae8915292a9ff42b2335438b361282ac20e9f7ed44e63b1dcdb092ba704cdd4e9fbf5e223e6d462da3cafa263bbf6886ad5fd063a6dc5fadad24aa1ffdffcfd80364229120904220c0223158418810636dbd51afb146db48517f7fe3f8ff5db40b6db9bc510b9ce01cb6819abe56ee800017f8da71fd3e7f477fbf27e9fdc9af98d7e7e3bba77227844728d441d600112a8ddfc55a8a843b6384e95a79fe7e5c6885bfeebc93a751f223c2b2b7feeb62adc4313b959f8469c38c0a148d13bb5d5f6319e47d7a4282fbf583e8fa9cf2722a57749b6e1db7ed60d75e945a3f4a803e261625e572a0539ec4cd373db728806a65f8e99f3bc73b76f4e5987fc693a2d723639bdd1d5dfb3d3d736e47e8cc3734966264bfb76ccd613323323163232ab0a22cb0a8ad5b0ca28632332664654466246619994c49110961919446059377332d651532464992061486329919992c7cff6fee7c72f8f97f97f29ffd7a374af7f866b3143799adbabf9fefb5fbe71a743b87f965d1b4f2f371ddc2bc01fb3687ec6be75e952e8e1c246fbeee5877fc7faf97c7cff8eec7c83433696e626423e2ef273d8f764b9579ca6f9f9f691fbe0635886188efd62bb8f697ce485d064e926e488635983a30153537cc2334322d65fd5fad39e30402dec3435fc72fa479d4a61f872d52eb255090a299b8d9a4848c8c2108298f39c35c73f29bdd563024643af15ff62cf2d435ae94edcf6237f48c1eec747f044d53f133141af3c2a93777a8653f94ee2640a84892295112400acddb71019157bacf34b39f39cb90285acfb71c2731c92b82131ebd783e17eb6f8dd7ca7778ef2335a3ba36b44831d5e629c56d4db87538e0a8d9de7850cc4b8748cb717ccc70238a18ab0da2eacb6df91df4386287f7d3ae5933e8c19db9c443ba4200fe25672d7ab24fde15b4b34b9cfa239918de8bd8a63d46469824e18199e141d13586d2a178e1a9508427c35a0ca7298670a52185563294aaa525baf3add30f09bf87f8a152cc9b3bbe7ab9a5b2bcbbe63947a6ec5e865f87cad9232c93a5f48b94225b1fb453dfbd98a7051b9080b6bb27b07e1d3139356d16466f4c9eaacb16737636eb15d9dc7ac61f2a45cdcb5cef1df7f06cd5df12be067d0906e6548569ede6569fc4f2758257dffe1fd53068b5f423f7dd836406a998b89d3cad145114a3cc3dd389dc78bbd54a216e50addbd5fca16b89aac39925a393904c28682fba5e69cce0021121d1abf265dcfc2fdb73f81e6df39d75f64ebc343c5b137ad9d6fc404f1bc9a9c7ba9dcf37af28d5919a666cd50917cc6be91c96492490cf3330688e3f51adfa622b9d41442cd8ce61d514a53103883cd241553cb811b959053f45fe74ffa7ba5c4be3dfac059fa9121c9813f304e4144a84967666e6b97e2efadfcbdfbefb4f7eb6f37f66f1c1977df9400b97e7dad5d0296ff29fc39c63960cd3da13565d783bd53bbdaae520a47aa7e3bbe7d1738cff9fe3ecbf14d776ff4dba233db683b25cddd245a0ac431c30e62ba7d49ee7dc9c517e1f7fbe2ea2f56bbe41a84feafafe40004003bbbafe90f1bea631faf4f061bae3b92cdf38822208222089f03e7a6b6b89248dde14efeb3b91d46eea8ed61324b77d3f9b15f11e3da67ad01b467d571f93f479de2fddb9d497dbe37ad5f27e3e3ab88c30c48e79fb77cd1f153b78797e66666697bb9b88c96f9d11e6d573f55ebc1f7aebd2c3fbe5da74fb46d32b255ca3de6d24cccc92fba66657a7b5abbefdf8cb924aae61f64eb3f35c999945ce2fb433ee0d51c82889248fe87b089fe6844689eea69a3e5eafa766bee8e747df6dd39572ae10f7cc294b1e89df50989c97d4a36e50c3a62228a448d9c882aab3e74baaf64fd3c73ca50e9cb2ad93c9441112a912116eb952d9fd9e2e98c4c04521e935a3e12fe768a79bf961a525a2ff8777ff1df74d3f74625ee943d3744a94e6e7892b5d1f7d4dfb8aab65b6ed24afdf96739a7ad636eff0f9fd3bdddddcbe57b7e9e74e3db578f4f0eb9f5ef7efd8747e0e3c78bbbbbb9e1dd67dcb7a831a9e73e6e1353c23f8fb6db6d3333284925a9a868fabed300da787ea3b4610f0e7f287f3ca18e1def964f946bc2632e9af652e84a133daf79688e1c622222294648e8ac86cf67e2ff1fd7a5b629167badca0389f3df5f96e6773cef9b78ef23531ab736dadb88bc87786e6dcfc7ceda9de5147769a567dad4f6b5a2b1c34b57586ab63b76efeffd716b98749212409079a481db64e144fac40f1fa9cebe24ef15e0fafd7afbbd37872ccfd21e83b1c3142975c050c23f5d689aad78296f30d44c210840b1070bdac74d9fb0d51a9c8b9f8e4e316aac61ff34f6e4326430f98f44924984bf544ef1b82ac2e8eb91618c73ca45c92660710e80612184ba615f02f4c3c6e815cb5f930fc2dc64c149200d48ee9d88cb345cc3bf1ec6b69eb3a4e42853431a9498ec0d69d79f8067c8dfc8e7c02d134b8e8450e5f5c4051cd924909249ce4986de65863743210c4a0217f36e60ead02a0685421410545c7300c46ad6016e41e9c3326c73cce8513dc9204fc5dc324c54b90fedede7afa75e1835d376a69f4e66ea78a5cfe9e252195737e992cf25968ff2b148159f876f406ff1e622b2cfe0ccfe65672659122a8a50a5288b3284c30ab76d30aa1330ac51966598665465651866190455444c659985332c8c933596d9640ad4069f6d6713e3981bb002830e786e302b0be35785c1b9fd26f9bd4e8926492424ff2e1164cb0e65d62dc6a7d6239e96dcfcadb30eb66e99b25ce8c3ad25e376d3e07f67df50fdafd37eaae034b34405c5b634a319779b618ea8cbd7d7d7d7c3ced4aa5efe1e7b72dde253e9b8f68c43f40e785ce7e3a1c29bbc32face91c132d353e8a05097d391b6c6ead8dbedd6bc73d6a4918320460b5d79ff5bd796b58f2ceb3ae252bd82f7ff0fa5fd2bac66e4d6ab5376c2cd185accd318018ddd85ad692dc64215aad5ac88a2b56b22b546624b94cb70c14a554244a8a0c8c846a028a40510223fe8cfe8e66cb8c0c61560b03add359ac8890006466edb2218d6860b7766e86b74ddd6e6eeea0b40dcd8b77712427774979f8f97afaf1edafcba9db3f0b75dd4e47b23c1765f5ddbf871a787b7ddf7f4de1ebc517036f9b9466e2e30563b7c092ea314f6aa6864f5d550421795478113077f91736ddbd2c6f17e0a9138efab9ca743a75eddde7bff35ae47d399cf337f3cfb1359a711cdec290ee39b0da12df0909251042a3202fc73c5f5b33bbf77cff3f5fe8bdff03dfcef8673bacff5f87fd56967dd063067ca9cd59908e47bf6cf2f98ddad868eb0ac119ec5b5b6bf1f298131bb6cd7703621d92a2674063566bdb4eea7615011d66b17a7bfb8cb8ff4afd9ba35a35e5c2bf20ebc0df7f89e8dc7a6556ee75edddd707e6fc2db91c83cdad7db83eb72728d8e639038fde9e33e6c8e33c1aac7b9b1e7d7cb4dacfaa933ed4e0dec3d18743821321d09d9c710e21938388e195a8dd046de2ff557e27400d0c1d78192c5bf047c868e58dfae7e7f1eff4eb8bb65a6fd3cfd1bcad2ade29bc7845a8bf6f9d2f6d3caf48ac2f2ad298bdb289a4a4613c9c3841da9b7adfb9c6d864c9932198c6603f958966eec466b5ad6b69ad69d3a7cf8f3efeb967ea1c066ccf266de26602038f14a69b99eff1e3d75593d19f36686ab698008368b3a0360f5a9edd89d06025d82f6c6c7e29b75cc8fc5bc79699ca19ba12dc068c2e389bfa7d6bb6ff1eafc06d18f0e1c4f2e7ba97cebf28f33b87dd47c9fba5e0d3f69fb59f11f37ac8e96d4f78c417d3f4a597c5310bda765b572d6d5336e0573a3707ab7d3a25fedfec9442510a6a7253148aa1e07a1b88c7b317862a01682e43c3c1b57bbf6435c26ae18d36605726e7122435b0faaf4d79ee117ce65919619664559545998a8b24a99048aa48027dbdf86eec7cdf8f0e805390b0a8a3a9eff7f5f57e4da0bf7ecd0afdb47819ab1a6f8939ba1766650266f8dccde19fe88f1433df3bfae879346d34faa2aa296273828932a3c501e0da90e9280824eb42151ad51cde99edccef5647c8066fe9a70fa2a8509713e9f4a0d202bc578c7d63967bf1e3444978b41d50c43cb124d03a46da22bc0fa78708ddb7cbbddb3f7d5861f86fa4e6de4dcacdb9172fc13aefe722d64a4985476d5307a64b6a37d6fa53c7724ed52077de528f4fda8559a7b1d049084264ae321df7ebaefc25c72b9dce76a6ecb7ef9d8d64a2513b56c969bf3a7f9ebfa6f633e55de43eeebd31ce8c031fc8c219aa57b8ae24db130058e9fd3a37d20fd2b5de955a135d82323c0e0c6dee518189dde293f8faba5f8fc0b766c77032b6a7c051937d18ddf67d3357eb7d59800d0002fb9bb3e8de35b885f84e7bf5abdd745689fd87b1edbdf086483202d657a64558dbf478cad6d16783c910bc0f17afc31e9a5828c237f39e7edbf554cef69e3b6fdf99ca39f673a9f419d8c22e97e9fa801fbb740000039ba0007374efc739de6ef7ba07ddfe9fbf3715a9f8f5adbb71eeeddfb699b31dabfa076d74d7ef35cb59471e8f5777740dd04fcdd5ddaabdcf7618779afbfca8b4c7547ac407d1e677478c909211eb6b5fe1dd0800002823c7db9c0c2b966db312000011012400785d737090830137776cf8f2b8126eb45d7969e37f6c551648336d5f4ceecb71f8af950e62bbe0c948bcc761addaa5b8f035dce406e03c8e41533cddac79794d24a34d3ad0bb86ccc98500f9998fbfcdc8fff40a3f9091c60dc7d42a5fa3fd28a42000a0408012112804087deb3e3e7eefa7e4cf0134b1ac14be03f1dec3390f9ad17ce10f592a824a1c4f0c0ff9242b93b2d229d86dddbb3ca24699bd42fae08492909b03a80b8aabcc769900e5b65d560a4f6d3a79b096dfd76df0ef3c21cae49ec275b4c4065b4436e97245efcc8b56dbc0e00c4e8d83839b3a7aa774ee9df71d5b1870e3936d829a88e1a3ec388ca24d35b89f2296b3ad478902ccc6c986632a1f9dbf582722afec891127f9fc139f32359685c69c15c1391cb1211802e05c45866059604ac5ad9d4a468808e212152a8bd912d6966d45a254a63f7526de6ac0f876d68bccdd35ad8f6ccbb999c667e92cb59999acb34bd948511574ff41bc1ec170ec1c397b1b3f0ffc1c3f9b49349f665ce993de64a632af75d7c6e3126629adf9d206f0b3987dbf7258c642710170280310827b914d448d1f8becdff6e9fa7082200219c70fba3420a100c60a4d4967710262c8407d0eca6f309b1911b963c76e20f72f4c44199f00624881f00ae8656e7642e592ea5e1a9f0e0e745d91be54fc7c4ca067437745dde7189dec3cf741fd5f05e345406e181aa199a85b210d08885c1821ac8450c946c6e830f945cf21491e71855a18c055ff603eeb06048a121157488b65620e459efea3bc34437ee6ea0fde39d36381d130648f1de1cffec9aa70127074fe8f4628c54cbd64f0f53516c2605221a0bcdb8840e07d5ca9770fbcc96f8ef53a7122d418c489167f8aa848126c01bf80a183b0cda8aeefba2182c7ba6020f3658603f26a7d80ba60a7d0e8316100ddce39bbd66a925ddc43a7278de3ecccd4e2fd8767a87336ea07a87c62910d1ddf87959c35f2b08a60ced4546c5ed02964181f14472120569b82ff0219e33d8f4ab9164c588fc26ef26ae27bed1ea896a842a554554de44d4340486a34665897806b14c9980151cd21ada7aecb573b05df7c32d4c5bef8a0370b6ee8e3bfa8df5f8dd868284088d268aba5094ed9accdb5a0d4a5c4b88784b8ef11cd9fc82770d82ccf417dc40f51e78b8a40375503bc1e987b3b2f4360d83d818c8c464183048a90809018b5ac84eb3650971305b1bfb54e2f6027b8a0a8a4625412858545edb50b412414a7ea27c5328f59eda985395f0f91f6d5e4f5434fc956b7b862bdbaaa9841902f120c908487f73b7d8051c1d63918683f0a003041449171188414dd78c0d37d05efa40037cfbbcfa42003aaf17f0be9997bfe7926f9f28449249268a9b90b1dfd5f93df09c6f4f0875ca5ff6d68ccedc2c4d8d1b42d232188a60ad6c64d3831b6ca5c109aa571888974038030069607bc8801a13743384192708680ec775380f1982420fa9d41ce40d5c036e54b440eca451246a25c0b2a91b4682124a4a6aeb508f06034bbf05be2540a1db32e77e840762ab56e1a6ca3939bbacdbed6596d9cf7f2b3b99529ef24525aeaa88d88010dc8e44c1c20d1dd686905a61478029f6fcc0a2f8ec720ef41fe347630958d021a2ccf815804b026f19e936244e6a40908950f996237012c022d4a888ef44909151a11ecd50e9c439bce27aaf040c4061a500544eb835b2ab7b67453ed1325d78c6e3e80186dbea11a7466ae09f381b0962f0dca7109028ae2dc2bde4b77806b490c17650d1b412a06b426c4b0cd0caacc3e70a484ce82961090917116e420d9106a2d4b3918d607fe3304d05089ca893bc635d45a9396754548cc8bc8f71247b0f6a571dd8b3f3cdafb5fb7f6210007e5755f6ea48bce04807382d32ff84cbe5ec9cec290b07608ab161c666e4d6b2afc266499065c56c4cccb2b5b52bce7cb2000ddb3cdf87e197575ea8a62e42a399bc1858674fe5940a04138005544ebd80cf5cbdd8fa3e2ba99eac26329665331910911b33b98d8e4d14b0cf50a135036c507b8ca1441841541ae4204031f56292732d54cde3b77371f039c6d07647943bc7de2f144a127d6925f62bf8a5e9938b492b119c64e572bdb3493d6666f488777e74c17fd2b3849dda966d504f51c9934d13d4dc9eef9bd15da18a4c1a6660a9b6d57a14d404986503eb6cb36a09c93442f8c24d4818732cad85472f5d476b8d958cd1894d200f532c45ccae6155ad52c3b4488d2dac2c10cef9c0f7d3485e7317bb5e12b599bf167c5f84888888892333146cac2490b2c18623a59ea05b0b47bc25103bf56d6f987586e6c570dc8c0304456302fb17acbc421b6f471b95c1d0ad7102a3c223aae65ee4a34c8770ccccdc786530cf76d8bc38e18011b1917b03a0c96bab4e055870ba1c2ad6305050a59744ea5bad8c6dc9b921b60c64be1a13a7171c8e26c7025614a1cf1a626b35cf53557098098d71848182f57c7beb9efaec0000f97d199c57bf173bdbefaf9df2da8a8184e14108cd1a2134e23cf81c9d760de1c0c1a1bc66a50f757eb6e2ae9eaaed1e3e347de3ef3171d5d656d7e6318b19d87566c3ba60949af11a4676cef17a196320cf3c3641717adeba273d76925649361248a206073d878bb08defa35b94b22c8c84821081501a2e6254741368c251bea698c33d6c3e2264b220594b174d1415b4ad826be519a5def7b700b9586aea972759787e000efd6d002867b72f9d61ee8a27b0517c70ce90a90905356a9ceb8d6c8a44fa0944be2a247ba3aa49f31fbc4ba8b235cf52f4f0964f0b59cbded51c0c151c6302a846e726c0d56a80c12b38b3533c01ab2a8114b24966352f75687c664d81a6832a15421ca23269b310622b9dcc28ad18790c278c88d5dcbc83b02a005845b762f7d29380ceb866b51ea0d9319264aec3681670baa5294ab40aaeaf2aaf3233dea19ea7a2d4a94bd8fd2a6d7ca906062e22e26645c728098771a1589487b8dc79047cca4b164edba250140cba0fdd13eb1084906d9cf9a51280feaa07a580f5430a2b81f2e7799b89c17d3ee08311b4fb21e5e6f9f76284c95d01cb191790d09563ba949a9a79551fc1c3b70a65dc85ac46caa2108b09025a842025b002846caf5c9eed7bb05955ac50916461353d93d50cbcdeb77426a79aac164e619c9ae0f56b51be711a857ebe3839bdf9779bc4deb275a42f34188f2c9bb6ceddac74507eeb9f3a92f88ecd8e347cb158a7c4161ea9e68af7301e5d6b3bf86db18c4abac43b6aa69e08fddfdd404e5747511283c87d64832079fb118a6403209f43989eaa8258353b42b8a5b18dd323d0b7ed1d05fc0afb7805de940e8c218a0a29a7d102d48c02f355f0a1265b3a9aa48734300318945f185105ca2d54a0b1c15f635d147d80cddb10807ea7a5fbf71cb530e26fe3806b12447bab2776abd20ee5373590620705134dbb6143089541431902309299f1794924f1e8de9dd9f50be53af224203187b824a1dd8df8304b92f96877a6a213313e203841721318086c21032310ee79af035d7593f9992b28c5c92cc1528d555a9452c230961559514ac18c58f8885059e094b644ea1ed6d0314d7aa739543bfb6ee516a27448bbac590b920488e100295ccee8e657be6d03a6325e6c4859e0e5855598a3f9fd4436379f2f310ad4c1c6652e860e5df0a6af486f332c751a50c305ac8bba14c754112b359334603741a4e107055cab6e336388f0102e23921184059109f60c0e212b15b97418658940603200a5612444c618ac4532b0b20484848c9108c2488c695276cee1acfe5d79cdb4d61b605b20122751c4b47adde72598844e8c0c128d5ae2a2e711f0ec363d61b432f2bab7997839014229c10138ed6ae4ad9536c59a46a28d60671de8ddc6e0f0a28c2caed8ddd3b81e64866e1ac37cc3b435eacf9769b1054d08800689fa9e85edc73296458f21b06801bc790da20d4d0e886120456ec6f363c2076df2e291fbdfa467e8395be9867df24f4976bce6621736256293d45a088d792e18b2f4b5f759c5d72abe464196d8650b9086b7f1c32475005f18aa3242040771496aa44ed03522253679994ef121e276360d835570d9a514cfcfa6a8924689de585a259e7aab52908055252421422d412a01240e5168b393367d3792632ba59b7558b516ecba55e25cbd4bcb408cbc28b1402e7a8a594391cdbf703afb8d04a83228007bfc144e58ec4026dd43a0b76ea0dbb12423213d828ea6c446bd8f34a84a604a0659c7fd28922bef382fd09d5eefbd4dcb7e058a1b40720c184e9b1083ac2da289928f5094a4294f6f1229b642433500fadb44b328ec772a65822a617eec03797036cd4d55b041d431ae7c8d4df04a8f0834b121211c8f0028cc1c3109310e312e1a8b8260864cd17b8dda042564d65c040cd48660b2a843e315732e060806d1d5849b4b9174c52b455552a66051000baa401a69fb44ed13403f02a4bb6a165ee24c42f4ba86b07091ea450a202c82950491029785165cd73c98334c5bce10bdc36a180a968aa6ac86502aa43230305ea8b8d8fb45499d1467735cc5d44cc3e1a1514e2d00b505738820c075a2203d0e908407a040e219a7b66313bd165c690a4212d934602c710b4b094d04b9ef89578b40a193e58db6144d8fea6ff635fe4e9172536053200b2bb94063a9aaef41deaee89bbd23b43917cc05321542a9bc0241d82e12273c41fe7fac75d040303a8c949f2bfd8e82e6ed08d85b3ceb582655cfdffe6f966625625ae5e4b94faa809f027497813b82f9961f4aa1eab2c3f53300e70fe7fc34540029881ff03ff00a446bfd1f8d61d3bfe528a8b47037ad2db1dfce18841f845a060014105a204927a75ba20cfc1b6d969382ab86d665888d174e86bc87f8896f981ccb90ed97f1c910f22df3714107a62a0a61740cc0e07bb9b1e857f01b4cf90f8f340ee04e99ee01b7193ee7f14dc2e15706115a60a678a10d4eac02802a51cfb203ee7910507abaf98c2e5407b0b64afa181cc1d264a9fa3d470e63b0079029f4c7389c229e2c6e058941425c1a2829483bd79f16cfd4dce6f99cd470dfc8b071e2a57c90eaece6a63e0e8b27318920322c9d691504b03d2a6ce46787231bda7e97da1dc4cb9ff8cb3ca1ce3b34127985ca24b5341ecf918602aea97122ae4018ac42b12441c69b99ef9e659b27e2731705e1c9b9b89b7de3ef558003107262850c4535aa2f5484a6acf65e58444368083e679b83b82f90672748f3154b7ca49024856661961931664c53149551519f72dc6ee5b4922962644664554920c92480c84def6f1f0201450147a8f60e4626e4fc8e705cfa3c42e9ce079e2f50369def8074211dfb979400ff5f4a11fb40f30d1093a81ccf79e4fc8aa6abc4cee411f7f6637e75f1f2995b29966a8b2b550c55165b62cd9bba65985849993264a6598b0a0909081803052a3ea16b90f53d63e62650fe6cc0804d296c9230c21785a06f3ccf8a3b447c9beb5e517cd53468b951781e8507b86d74de440dd690612421320e88fcb8c320c2c0bb0f2ee506334144452202ad4472fae7bba593f1dabdd735996512a222cce2cd8b24632425e90c1b22e47b8026f81a1e999d73d951af8a671e1922f3b22d4859c0aac17606095eb7fe985e11b199ad42db76e1292c0cf80d25908408326a146133a24b304c25857f281d452dc91a52cb8bb4103a20dd81245918771c8764c84c90ce3c38e73207447f25d93226029fe314da0c8a51e25b64232e88409b50f1b8664b62897ab65b0a2125138a213721283da16e6181b0a2bc0b8cba243512aed13180528112f51eca9085b237c6bf275789724d9909bed9b3e751b4ab281c9be8592e80952824ad357b8416d2f1097494329736d59512a484088230895263465107770f60e061a9b832ea3157f4f7bbbde1d5c9e1240aa02420c4299083085462273ef0d564cb7bb433206ee61907cceb3a964aa4840e819f35c3abd78c362a007f4fa506c0c910900430cee921cb0589ad90a22ac50f27782ff8396e9842207ca3693b2a114c40c02e118f9a94551080c2df355cd90c1d499ee4080f1876036c81715f80d20281e2be44701c8ec0a74eabcdd4492f1d67c96fdfb7c4f9f512c984a65978dbe1e700dc03c235261a5c4b0884305198ab248064aacb44265b07fb07e00f499a5ae90a28ba2e0993e243c02c101276774227078e94706309231a229446ab2ec2adcc33b076290e4411ff21483db21c03e289d5bf2379dab16b3e523184630870af04188477d5244225550d3cb44a188dc8505726058f1ac8c0a91b85b3eb578b9c2f8efe16f39c1113cd99ba5b872aee0db745609ea81b0420240c3f4dff36e8320a493e671e288fa8697cb93bcb33768e8aa08b8890a494518946c8a25821c580944dc8d456bb98220938946311498561986625c636b7f65cbeb2bf465f343c590bc6667ebd6e74a8ac2679b6f6ce188161778fc06b031ce034818923a20a5101a0a177402e0c03070cefbb9f39164d07886ed4e535f4a0ca522c895687320e74d41ddc2795d9894c769ba9483895cdb36cea49ff38f0d51d38e2f5369c4c549b7a166030122931423a8ee44da64c432d5162c08046389ace33485ee64c19689928a96f3df06d0ae41d367337489bc4e31c12e4c659bd3198afbb3ddbb88319254c3db2cbcddadde6eb33db3a57acc49166473f90d78df52af867325ed9679cadfad38c6252a944999d2d993545460c3971282e3c4964d44c135381695473016f24711485fa857ad250122faa86e8414e3676e25105aaa81667c5ec867c9bed073a122490244e68181b04be27e9c304c6b8c5c590aa6a4b22d98032559d0a363456c19bbb0a2102caa682154545c457710c2519992f787321b61a3592ee2d35765c46031b83502a161052d60124835110e820ed78491504b1cc402c51131b193521143527e3be4caa0698c5641090058d464640905631492461006415170685d8a14070b85ee11512880826006b20f0d6c72d8d1c394230aba6aeacba33d458923da85264578781e2a647274138083d040c48e49b81ddccb9b4a7607aff6e286e45de4137bba0489e21653049100a5b7a582541224423a0074e9e98c9ac57c62344919112110630c4901298b501431149e2128910715462ce1742c5d84e0378042a10290b3815a0d02d0e83622c6c705e580ef71b16c1fa1e6d44c2424ce1fb91e42cd6d51e0e458db1b25bb15451b63062354fb147e660dada02041f7868aeaebf57dab9babc8d34b9fa11a246ac5f7f5d68878d57d0291081892f8b60e08bcea924655026f2d918c8b6c42c4b128c87dff323f8835257cc5f8cf985ca4002357859e32c4924c592194a94baafc7f8ff4c4f9410f9c00240aaa6712548428948a91880488120daa92d45489b6bef08ec1b0c32b235f94ff4785d51deb0d1c2b62402e25acc09128fdb890f95549e004d4fd4572e079f6182b46707452e0b7020028dcd07b226a6faab88122207c229888ac88a3f98046a40cbb922076043fc90561104b8dd4009011f53d26a34c8fa8515fd5321a20291e1083d42de01d314a53082777752e5cfde7684ba3cc7aa951436c3a8f323dc908c249c0f45a00cd4b21ddbcedb16aa090ec2cd086d7a8e12f8309a3fe52d6086372ba3f32c7acade414a0908b176683dca67f95f6cf10eb5e473a686f50db9d13b83cd3585580edaa812587c8a841dd05063e01cce81b1f30d2531f99dd2fcf01f47b9b09b786325604bb4c93c8dc38e1e618a78a3899e9aa9c80f8702a353ad0eb35e9c54f8167f59effd024d5e8399d9da850d837c0e313ef39244b15c8c2c323d6005c81d7455fe12321d301320731cfd3487dbf7a3d33f0acfdbd1f3c9021fbba3337cd595c7677c92c1c65aa66f7b955432e95aff7dcacad7e75ebc374d6e9ca138523e005014451973ab6e723a95d5d4cf678eae63bf357f1dbc9419281f8143418a2e0f90d440dc9ba410cc1cce198ed981f4c0dcb7ace1aed6e681510849124486c21c4ec80a0560c0d86d5617b512a55ddc582839020dc24febf654e6155022039b90e6b0bcfcc644a2837d800fd0d875632492501c17c034e830028ae36555543201f6f80c234a690d794298404f2b4a15f32611e12a309caaa3019149216693bc43da035a103308450819a0de99429442c955551980a7f480f81aa3268a21123e18b36970412d30250c226041a3d8ab24e91b855a21505a2205424aa656415755324a318950a83b9b25028db8a5911cc52acdc25c4db52df9ef079ccdb78de736e195d2100769490090142e1411515a80fc889b42e24898aaa22a297004fbc4c1144b0894412440f40fd20458484605dd8c00fc4f4fd432017b89cfa948f13dfd20e5c4850602fe17975f0e786f682a29bc102d1d943a167bfdc77ab134b3643e500331fc3caa1332b44f37145268aa03f0b25c6c91c6a8c00c753227a289625ad848aa12c750908714918aaae565f31b9baef97489d46c9b3190a8ed20177c89d9f527482710be9421a2015ae4a1db15ddc021191cc005d5ebf906d82a04a123430e35d9e4f91182170b06fa05bc068c0038c68ed7b4d2b3e2a6035d1d48666d70c7750a1d2413380806f300f308fcab1cb4ef501f0d80845df0d2b7001ec442e670c1286b463ba5d65a6f3a4b972ea5cf8631e91e0821a8177b9bac56f6e9143b5206e8f24399ddcdc53e5c9f7a44e62e4a40216439c8408211b27c8b706601b94214bc98e88125c51d99996a5379cb9b72a2da6aa4db18956e19b4dd57327fcbabaa445c6c56ecb192b3acad62d1418db60841f43c089610c1ea06a54aa251523481dd3647d48f87999e7d8a44b0503e1749a709a89aca2cf4a620ca7340b970b65852c3f7fe185967da8a0705f6cc33837ca5f0562c195b5a19d6cf58caf88884b244084973f5acfc8cb56c84d7e19b3e9c32e36cedbed1bd99968532b15cb7d8359adc7be0621a956dace24454b70ca8d5345b673a71f56833dba172833982bae4f0ac26d881d625d0dbd31576d4bdb692acf400dc81081ab2b67a6d9ee6c18ca9d78601c9d20e4d04e963c8bd71db1be724522099301a12e90ad8a2abe9abb545542b3caaa28b29ac49ad3d4b70feda3497487a42f30a662b8fcff1b471ed4c9b7b6f67c2290b87cf11f74b15faa47d2bfb34e0a088b7521cb60778a84cd67bef9303c233b779b6f9610326aaa0b26b48580607ce06f8f33323848b028a0e26f4424df5adb3465e64660717905a2d5aaae2dded1736219b1e928dcc43332fadd5752afa88cafd9779d7407aaebd4c2c1cc5aa3517ab03014b51c1abbde5477cd5c618a84db01b602b34190a30c12f5be484d1218d1459a49b5dab5352ae026f80711dc6a1c5568af10ee33b3dab5bfa32da51d7b30be2865c1ac97808b44cc3f2d5218215b522504f17f3ea1c776334972f864bb25cefa411c94350c42c20bdca40d9ec7d050be6c20b89cef6f9737d43d7f4e06373b92ae4ab6cab902542532c4a2e0d57aae101b9d9c6682a84b0dc3b17ba434c03d837e4e899e698c0c133874025ec83930197a258f3e127a81289a59021136aa15298510693888360f60bd7914b849181a8297a910b41c8da145053e295213258932890d42954692a4a8a72089229440460c104a21dd58b6124371fb7deb86fa3d36e7ef1d1529dae166ca518c46c102481c9ee588a53d4b6b3f170f87a856db5d6d577fbd9618d599879367299fb7d5f6c149b17515980a45d44f28d4890a2648b64d7eb8cbc78dc08974148d53423c2fe45dedd43589386d6ea969396b06e64e4ed5b67722f194fbba57321a39d16b5af58b8f42502213b31310d13df95296ced136076c80c6cdacea5f9579e448f8d522ccd5e92c948b816aaab36458bac2f077dd4df2f12cb178ecd04211379c6e8ad51668d2d1931c20c88a4836f2bcf1337b2939bb946f251c236a84a85eb333d77c5becfdae24d77b53a910ee17b12d916691ce6e42e4e7625a47261b8484954c799651a2039865411515c50c546c925acb5a9780a9196095571d8990e4ef93cd58b884654a54dde13eb5d512945aa78b9f91c91d946e87859bb6b81ecc264683d80a20bc0e3211ae06702528bc7897c30ea0e22120c93558312b2493847ef39d0a2926a7c55a9a5dcd37a96452488b11ad0889087d03871757a99a93e44c6b298a60d759dee49aea0a5cc6f52a16ca8d982ec456f17426c4bcc8b925ed81c950ed9c86da35384386147731c72389bc4b945bcc82da42786d4eb6bc64ceda3c3937ca19ebc29491df9d9c3ae51d8d149990284429e75c42cae54b5318b554b391b61360c6dc49beaf056b7133741828ded05326f0c5fa0304def62f6d8dcd83243402ee8017e3b0be010ee69530e068539911e63e2a34446111424610845019014bc0b2230398f86e5a49516a25552d6ed17102899985aa44b89510b80d111aaa50c4692b1b574ef5775164c6245212c136a7a91c50b9391df392ef376512f3b6f7a95eecb7333f3f388c177d2a73ea81881954874614512a673685e7125325a83784c752942f0d23acebd85c436d09383498af77505feaeb50c78f9581e040743c088ed3f194a03e5832d84b1e7cf038b9f98778e21c9ee88486c44da280de203239545957bff067747dfb6c219f36479595193691890aa24316517777024520041af1a6a633b9def33799d2b5c5ce5ac666260261311805b755d4d83c15f6c98278c4dd124424da20d45a8230e278052ee355eb3e8591ec222eee64e6460c7b72439b0c00540d38e0f4830612091342990d10c8df44121a1b088c8f60e9df3570bb0ae145ebe993264f757726c2ef7a825d15514a86b38683a4325353a79f5301800bddd81c1103b8e2619091118790c50760685e52123455b659c03d1363ccea5040500b470e843c84031b900e46c970e90550ea19f1df506f9c05a002a0276885c203240bec4f11f4121f6fec2608f062f31d9dcd748f60873d241228102db8dcfd08d6fd8b97bd444a7d104b2b4416db09da7b953ec05b07e6715534754577bf1428477a6a3907a9d5706ed9b5a811eba63f781eb6f981e9105f353a0c40fd902e22825bcfea98e2a767867f52a516a259b4084248c980aa814f1346453945914345a90fc66612048b88064c730d1f442e2af04b740b8847ab4101832236882734e78a5450a817203210e5843204fe2ee8a75d1f7795b51b8555d94cf6288bcea8a73b43e68872094e6903c5b205fd271aa027a8555525010a0a9492566714dadaca2aadceab5550665092283ed60e72780c9ec3925e503a91500d0ef504285859faa8dda2d47cac6ed3278e48521a40908900811611422434285ee08730a5e20a838eaea10fa1614409230b205452da3f0ed8b89183f22cece3d8f9c8f0de19e505f1821c22480676ba0dee2bdfa7c778670c043f4a729f1a62b240a0c20489974973a032bacaa4babbfc2e2e5da61466637646359017a34965380f797ef343a8891ba3c6cc997125d112b185bcb3f065e80d1b2218c1a2a7a9db472ca11372f3c67bb9eabc5572675de832bdaf23abaf7ac8db9a4288e61db26c5e6cad3e786a370c436b069d18dacc5ca31a2c42c622f596e5704a3a4f3482f5088fb05099c1ce9b3de03ffeffdffe4fff8bb9229c28485445e6f38'
default_vs_can = b'425a6839314159265359c6ee8ece0056ec1e8040847ff23ffffff0800a60271e3d71e807a146868d0a000000e7ca0bed224d7ae8a4e5b4bb1006ee55b50bb7754c6b6d843ef6744a85eab092b4186b6ab4f61d766d2dee21dd01d5d6b6dbcbcce69cae9ef739ecded6c0e80da975e1a684348c9a013493d2a7ea860831130369552694f4651a4335180101a6404220a6d53d481a00060934920404991348000004494c83530826913d49ea3d46d403089104201084a00000e10151c80151f5fe2492491505f88254449112444911244491124449112444911244491124449112444911244491116105841610584161058416105841512444902492492e80a8ccc0151db7e79eb9ebf2fc91dc80109008c35001c0900210802d09022f5240894b8002ce480bb8931675a30e2b23f2715b0df44f426eee456f791b276b12c8d78eb7ba5c2d65e823727566af062b5d06073573bd6f7ffb6186f355adef7b71e878026dfc7c47a3e1b5f1f1f1f047a02d0011e8040808f401bdc047a0b42008f415c97208f4006811913f02bfa52b434f235c6912d5dbd4c9c4d8ad075d2b1f50f93ba377765ee24ea5d5b72f6111fbff59fafe7fbbf58fe9791ff99fc7f11e8fa7dbeff6fbfe7f8fc11e82b80023d06f724047a000008f41a0723e5c7a1b7cc6c8f408384371e86c7cdb6e3d1ad36db6e3d0081b6ed4c8a7962a315eefaff1f84395a4e022a9d4e16ba5d12e153c15f4ba5af8709c54354554567110ab97edcf2dbf63a98e07cee74163dcfa081f1af3cf5cf59d63d0dbe63647a0bffd7c86dc7a1cdad6028f41292e4851e8e2ec00d8f429e424851e8ec3300323d0975e891b1e802f800b99fd1b0d4654e7e9bb03debbe72f2fad75bd92b5f14ee63465410f272ca31cb64ca7aec7ce2a4efefb18dfa90e59c73b8416df4d31bcf6dd420a4a94889c42841111802263d021242263d02492e0a8f459800898f46cae100bde859c92023d05d2480e8f41290209c8fcdaeccefbb71b6a9dca530e39f87d951b992ecd9763bbe7bad69233aa9c37ad6b5632ae1f515a83a27df7e3dfa4fd9f218797e3fd7ebf5fb7db63d020c00c8f416ad248d8f420b011b1e85200028f41949701d1e8d3500898f42c12d417ef4700803de84b3520ca8bb5b99fb351076ea58b7a3edea70a42a7716cdb8cc7ad2a70db26937173d77c34d63c6e2b3ad49c6318c0c0418bae78c69a62610442ca93a444d1044e2714a07bd16234023d0a4480147a094b40147a330b380d8f45a5b880c8f41200028f4121c90151b75dfb371572366cd5f6c9cb86e5c16ea4cac54f989d496b78985ad417179aad215311ad6a266b5a07cf9eecf61859df7d573aeec341292e40a3d04a340147a09c5c2051e826d000a3d048000a3d04f200147a094000a3d04890028f4121808d32be7664cd0da36cafaa8772cda875043d997145f63664345be94c69cbbee1e0749aa9be603df7decf61859ebbebbbebbb0c30bce65e6586186eeeb332c302507085ef425cb920f7a118001ef4700007bd09720023d1d7a902263d1dc2011c4cfd46e2ab88efadfc2cab4eefa35e3a4deb330de13c1caeb49a9716c57378cdd7e31ab72dcc9f27cfcc7a3e3e9f4f9f95f2be67c702047024085a724000800edc00046a000ed000397200ec400184cfd03e5b8ce7f0ae79de6e690e2ab4de78ca586bda5dd49cc4c3ed50d3d9eabeb6dc18eba19012be8f3377e02a7e3e396b432accdcc0994926d4acd369622da7b32ebba9ed3907b324ea6e4a796d3bba3458bb5bccc8571cd3ded7dd3a5b5934ef1f7460f804936ea66a65b6dbe6db77733a6a1556dd14f296ead9d890b9d78b36a4bea0d79d4efaf72356bc045616a72b9c6a655530418dcd6e46b8778f25a5e69d6e69c36918f1bd992e1beca84d527b8a7b95428ab7b6c9c7dbaf0b9a31d4c4a5d23b1ee6ebeee9b8ecc96a6adbe78f32f8b555903bb2d556a1ed24c785aa9743019ddc375ba575653be6da8c1b6db4dc53f50eab35b70ee18a953d1a6af2dadbd5b73916b0cca964198cbd258fb9e674672e77cf6ed836e65b6dbdbc99abab9d6d574bc775c9d2c9c6d6f4f03be9771bb4b24aa9dc7bddd9864b8654b7cdb7836dd1d938f1bc6ddecbf4d2dcc3124599554132f9b7331bb86e89edb7a2498675d9bb557ab1ddd72d59dd57bd8e9f8eccce7dcf9b770db4dd36de36ccccc6db9a99a78df74f737b1236eaeee191dd7d44e3ed32a86d27c9e4677035d918cc097b7c9fe9e88fbfde63fc7e97f6f7ef8a1f7181722dfe5fe3f460c80fca927f154e75a0b98488556a9fbd25828a60ed964e38ff5bbb26a5b4b5bf4efb1dac3fa4d04042762c0ca84b6322ffd40ec3030bbff4f82d37d8b0b0cbf51924915021105e60a9de22a7570a542a02a12f4868c443f2901748ab2029e21cee8c9c14835012c888074b979737f483e03d9bad753aec1c7909c58f26139090cade78321900d26bb1af18383832286200283ee02c86c7c783617dbdac9dc28ec440f1af0a7146d43df2e0323c2bd5a9de1f693701640a21b2fc273d3e39decec060a52b303a2ba433bdff26aaa163c6bb65516b64c066684e90bde8ca8d98868b9c4d39a3c151c688c96fc260082b0dc108772df22f61ca9ca808f16a6cd8727b470fb6803d750a4fa7227fcd0943b7049f024f86b0abbd2541763cc00345f9170a994a2f321bb35f8db062faf862d36761e509c518ff458bd6928de8a50d0b13cc4bb36d1a3001f8fefe53ce0dcb23dabb5abe3895da3e0808a8f69dc9811843a403c5f7b4c16e4d81500d6b5a37da07fcd1b6f3e69b22db46e843067049d76dd7242a4503a3b8521ca6e6c201c09d22778808a38f9552d90089a1e8ec9f223c18d11ee859c56fce26bf558c6a5b35b7045dc4e487730140408795a291f242a1501a7417cc4ccb188f026342e73416a428bb1a091180c2fc187117f206b5bf3d72747adef2e6abc5e7beb31ad26fb5cf9a0053e0e0c12d25ee23e79bb66906eadf5a4d390ac99e1035ad57204000f1cc94fd18a9077bd4f784f9311443d9309e489d8fa129d30a6c1366420a92ffecc83901bc13f0346f25861cc9040f91904e4c9f0380a731d695aa6ac8806b1f39a2f0d1057a375880e5f85161a0c6747a8117eb83b14fcfde93103f27508a4ef6378d8e6cc397c1e0b39dad5de81992ac3f3b759c30beddf83071bac722862bec810e0ec16784b3b25ad44ef11909109de3418e4e00fc1e65a18329fb3458587d05276307660a788883a3cd5553b015f909d1c6dc855dc52b322571fd3acc643f4b77beb8c346b2f90f5e15aec2b74b8a40e59ada2320995bca2191ecc705a1320d4d03e3c766244b85bc959c0bfa307c81310a68761b9dec31c80b2a9d8b214326c7879d0fde7621e806eca524a2738e4fab188a80e4a022af31f98308623931d580e11041b61a1a3c53a9e0a4bc553bef18c1bc19cbc6bbbf0f4913a436c4d14289d0e5b480280f7f2484845397e8afc34dfb0c219d9ecf0b515d07dc5e00dc289cbb147096ee2453b65c647c6b39d64bdd0b39016b309fca05d0ffea69a7812a21419816b742252c50517a92e877dd0fa12a8a1126ce0fc4300b88057e850e301877840c9bfaefc1d5ec3a9a28c9f5eadf63e4e0aa8c9b815768f944107ec808c3d3765aa7ea8e87610ec0bf00018071bd60f52dacea6e2de21f71158d5b966db543308f1e206cad215cd29c02fc9cdc4e3d4405b75a45818b9e401721e22753ac99665845d36b9c7a2712e44689585a101733ba77c01dce6c074463d56a76f3be6bbbd737a5417d084deeb077cc00c2046b62a4c40f000a040bda7c9df5bcd020a68acdef43603ed0706464db0629b4e0d20b3b1c2f29b81da43c97940c58e535ba967e23d1905c883a03ae30e5f39941e493d154496e893a434ebc716ee7a24c1b0c73431ddd4377aebca812d1df04675cae33c9142af148f172fbf1c722f41bc3030381f8157990972a1dce3ec015b0b9ea2c224643c43dc3e986e6de1cf1100b248c00e01f1cf017b3251154901c0a7d9f0a3d8e4ec073c27c9f3582f7acc2f1e48078b0a877a436fd3d8d5c8bf39a45406160d347a2e15b6d8d10b6da69c50663aeb0de420c24d0218f14b45184d369a8064260eac0e89de0b50df544e50d715cb5587b60559354e9921f6bb89482310fbed902641ec57a7c190446d109c77c1d087c25c4d89555d52d105e578e307c1433b885a361ae10b304cdb5ee34164a605b3b5bdb49a940dd0f4605d0cfbeaa2a6cc19fa13621606a668dd5a1fa3055a5130d7e6d3a0e4d04707dff43ac1df93bd840b3071848908c8c08484920e13b05f02fcf041ae9ce93086303e2d7f240d9028163bc0a6d91671db08f9a0e0e264e0cc1e223ead4b03cef450120350a60e483e08bb963cb03d0771f77953ed08150e2e53d14059145123e261934e85e5e0887e8e8f761dcaa5f50af66effaaac1226cec243e445148291100562a118093bb86d807938f5903e36343de26153da7217cc48ab8422b9288707643483832c4f07884ee81bf149f479e6144f164a748f936b1afa0f9cfc4e0a92a76b68bf33056f746691a45505443b774d1105ccf893d1da5c0064e1030b4290b0184a16d629d86ccb167e136d6d90d13f3efbec4fa806e0547f66c2727e3f791fd746364305a25c52124adb7dd941dcb6d1c27708a3c1bac064e937c44ececc6c5d27a3bbbba3b14fd047c75b0b345d078dfeef37cfb76de142cf45348926a6c9a19754746af1a9ed7ec6b03e3b5fb9534513a29aad06e1d1fca3dc13f2126a27bdfab1de87a61bc3e897c4348790d5d1692e6136d5953c873df5341c6abe86e4b79f1d6140de21b5522b4417a26507a8a1432412f54a671f2ea108a8490882121bd0aed4a3d14240dd204cdb4dc014219b664820cd092a421462210d24cda84d6034816082c24cd2d00c424466464d125b70d5acc4442a8e372044820515805048488a83885440909b3f839f67041a0e1b2f6373f0f18884439f84f17a6f93f987984979dd0246420e1b301d19a1eea6b17f8b7e2c9f8502f6eb7e718c6318311123441368e7b5a992df5dc0c196308902daa2aaa88920855533130cca934df9a6deb5b9640499acc8663d6252b098034a4a009ec154ba9f3917811d00827191a6048744f87a1f38ce36359fd1789db46793306608409a42eeb656cb82393251cd9ab3059418d34e26638c57e9640c8e91501ce0e63ae2d195091901200ec60202032494296cbaab8371f917b41241b82122a672c361c12417393ec3474a484a8bb0b683ac3a2b418012c84534110824525ad90805dc14106d25825106441dd6ae7de9b40da9a1c968624a84861b14b01102998b92317636ba454dcc3365c7d9f7636884914cc10e0956a280cae92542b216243210def098fb7176ba091d981ba4149ce38b91d27ca05d3067a375f75601dca9355fa10f9054a3015c7f3ef027dc788b983bedd8a2c1e8fcd13f7a085b85dbf8273417039377f98a7d903bbab1b9fd5d827c4d3f0d7acacd51e850c10b2d0342b4b80cc54ef15c15264182aa959694448519b324348a0759d93717ec68f6fdf3ec765a97873d38ec1643c6b753a6d60f7a7b21a95353483a0d635593dea1ae087e4e4ef3468d8b21d6c0582c1872dd0a1dcc270fed24311ef884551fb276b49eb234f16f933da1f1421edce75af3d5bb4d6e52f459db15fc3c23813f915d901b94870860f87f3f926748615dc7f1a52c4e0fad6d75699f1c1429c18bbdd51e45e0d90bc0921ad97d9fea8f59febe0983801212030209e7da4e7cde07b4543987fe453262548527b8ead0c9f80f264ebbf3cf708fd9fcac40d84917b1ead672ca8c672d41c2ec9b6d2422031541011458460cfed4e9eca6f234b95fda85a228dc5114d683ea6003f613d9bace4de0f54947456c2d0fe1709a8a710a76c9cf2ba9851439b3d26b083d84ec3021b8886cc842e4f69cf73ef02f5e4a1068203e37252864382522901083ad76130104f729845a894810201ca1a67744a275677a688721954b01b04edafd8ae684f24ccba3c06d3f7dc268af2e7c60420d546f890e10c47c0ade3b7a07f2e0d788f8d0999d0100e65470bd89dc22d7770a59164527981c431bd14d8413f213757333125b5216c394c81852af2e40c303545263ad0234d64a793bf061a5ded556bdf25996a0d065712572296052959f330f2981a94361298d9f0d20c3d9a330a5aa5adadb6dad5a6c0d93ede2eb4aa586d6be18624d4f27898990342468da4b219264faf1e0f6285943ef44a748380228d4487d2167dc6d29f79c3e7bc293267dec30c11b2be4a0a25c528385b0e90f96fdd9a8e8e68380b947511f22586ba5d9f933f066271eef57efc5dbce3633d383613797793c94a1f1a37b4780137a7f378c930423aa20110b5285a059da05003c160d57303e488944147ea01645014c1132456c1872704e23d9a0ac0d38b57a2fa87fdbada06f718644b0e0cc14545e69470067d28c54c103eb48500ceda835eab77e836bed0151983dfdd1ee577ed7e8d43e7ba2d2237fce2c5711f59294280654e24320d2413d28c959cc3939603879d8e8cab7167aea7ae346b73afc79a72b7581f121248a105082841420a105082841420a105082841420a105082841420a105082841420a105082841420a10508284161183efed974043c97980a4ee4b044ef4bf044405be8821f835343493e10383cebe45b873b7274cc1d2a277d64ec293907e40242c1d07aa329744c118b1c296536a91eb18ab16cb1af05ce0c8a3988118059941066ad8b45f5803866fbe5b346da50400dc870150efddd13b954a8b2b3bc407cd2a7836a43051536b8dc2596595601caa73790ce6825d435c8f6e0c1a2672673c93ec53c65da019318a301e0e661f8135bcac115137184ccb00ac3a2f8eeae82166a032e8664f9377ce561e5efdd4e87c8ff61ca05987b8683b96b700f7096b4801e856dc9c5d7908431b23b71a7ae5acaced8b14951bec400e1523c5e3029fbabf21851006144127d7870c650acd24111422e24aaa9018eec85147195160a88a691cb2182131a2a128c28a785cc98aaeac9d09911439940842bacbaca1876a5e08956f2efd92a1dc9d3b0a7136208a22a4bc63d30a004d1250beb5e40deebcf060decbcb78e84ba49a514d900aa7486c8a12a42977400df70061e9bd87d9044ef79646f660d9100c5c85d0c1a67d12ee4be8a182f7b01519a4c586af8cc9b2aa4bedb318de2eff3ac91bf9e822d8741b14fe00bc909090934b196d0b18f4fa1cd1e0cd7903938b9ba5790806d8468b8141d60b7747263b9e4d0c5d4640ed43441686152422192b06e83238eb405c447493446cec7f1d5c1747b9f681ebc05908e7b737f171dc5dcfbf760da21d8cc52064a44106c3c9ab017231c70e82e0542d74522660d15a1303460c16507e86ccecc89da060ffb40546163d8ca6b05416123cdb40ee80803f1c15a3b0b48061120fd957e14df47b993adb59b25bdf0a3034ec060732821dccd7e34d806e1c1ba26f0f07427d70e51d90c0a54c9f5bf3921bfd58dfbbd26dba6fa3abd9d0b3b8f16c2a509acee9a08929021f70f007d6bdfcce3bb04fda888774912492dc0d243c027b2c49e9c975e78cf1d67aeb7e4d6f49a5e281b38886199deb41934254eb96a41d7633f583a97fd848121fd58e7729374f96abf28b0f2649be7a48aa7e878f67ec4f5b7589f07dbfa3f82d07873d64886f645385059d99dd9f1d75e09014c4678a7680a8c35d6ece58fcf27ee2eceb33b87b3f190d1ee94c13da7d71f1d068c89e11b4f5de4bf3b94dab23d3801fe11992fee9a1d83b445035654114194d079aa1f5db855e3f3e69e3cd2c56d9b099e0c8101c023722e6926566214f00c33cdf6bfaa26d922036b1ccb073c4b799e5574a34ac888325622b46d84dfbaf7733f086c1687790043c334920815d73105f25859e55bcef76e2c3917eab6fb924892b434db240d23bc0a867dfec6481bb8a0da27a9d75be42f988f507b90d473ce9d21c6cae501236a34df22053851febb50be12dfb7fbfe3f8b427847bd7661ec82512f7bddc20f4679a86edddf3554e082401d14a1e481d130b429c2029b6164fda959b5b0d30f8666b6c95e5387064ee9c300d7e69e09faf8c167764524509d998e08766abbc2428f554464d7cf9f3e6e07e7bd07326d44a31f5fb5f9f74e5f6a4e43c8cb0fa6a13bb70940050851810ec90c1224610e44d09825b541d8fde1035bb60341a77a0d8e99c1282a7fb1f7312295d8e9988fac53f966af39982daff3c5f73e1c07701081a09c1daa74238e839d5d422e81ac5066d8e0539652665d67c6c71fb80f19c776b173db808dc56f63e290f756f04712ea8202616b5d13cfadec778c39b3cc8546f30925b55abf6aa765b90b603991db29d30e146958a2c5eee2838de34080dcf9eb902ac2a9ef4d704480595438fa23c035d0213cbbed044120188778420ed3439250d69e18baefdfcf7593b6a78b717ca8a0367821d43fc138aef9f70bae29be691d4216faabc435b99879b7043d307a88620bf5f7fafafc7e3f1fad41d9444eb6fadc4b5123f48c81f568103c01793306991871f59524e1eaaa73758a7b5f39e499da3a913a049148525617cd20c8094952c406da69b919432a25cf82e27f896fc17209afe7c60a23905d22498811104820bba2138090c25e9d84cd89a19a9e472b9303af0bdb60d1537ef4c5ecdc8c1e0a5e5e935b5d8794e0c90a0735e1be72241257b43d06704aa01401f466282d3cf3814394a894c543e5f2143d32b0714b09ab6428a7e2c9a4db1095db02a82c90db90343ac92c48355eadf80450ac64e0fd88f1f3a96b2d2d716dcb2cbb3bce3d509d6e568ef1f85d79f6345fa64102a4dd72ded20e094d01f8760bc30949458b70b9c9651b2b1a06fb5d40aca5dd56ad2a7bf3931ba3111abf369a6073c1f3a31729afd69c994c881122de1fb4022088877808417797672dc1dd0a1221b578ac8dcee32d331b7f8a50b8529cfa187277d776335146c9bd59433e2d42dc62199ad36fc6615554e6835e2e38ee5b7539ec46c606bb5c63aa50b85299e83a7276eb56388ce89732aa80c68a4aaae1833128415a1ccd4f2e3dfbfafcfce23bbd467b797dcc831c38089b309e43add7bcd9864288a8c20588420335f36fd2a84b0093b0be551f05c387947aaed8facc4b2c4679e668e343ac3869108d480e5d53dad727c39d44eb1683f496ab78bbcce12e34bb15edacd71faf9e79e4e905491d2851a975509ae34bda8bd8057ccd79b0e4a5a4b973a578cabcd9ab4f055a25163ef6613fa3289da1246d393c88cc869fae3d7af5f9f96cf75ef4d3041ede7c6a231a5eff9fcff34f99db3d46a557570cbd2edede616924a4a45d4390789aac315f5f1e9379435ebe63af3bf3befbebae6da2a86f1461ff5d3b18e3f4c81c11d4a07b75f4247e1d24aab2f5df6ffa8c9a80fba80bbe60b7daa36fa88e6a2255a9a87246b6615cfec40a016df20d57ab0e9a590c59e9f6d7fbebaaee445765b5f5a62f29b52b59b4ef8f5c67e2b8438e0c2be0957cee2aadc47bc7b10fafcd6680bf840fc621d090e84dd9e6eb5e3b437380475290f4ff2e71f7cef6e75e71db34cdb19f4e203483e0ba74e51a4293fd99661267fb1f41d873f9e35ef686fb529bde4144221dc4db3d7938f2d74ff3b4ebab9dc3b9d1843f4438e29377b874053ca223329520ef9d97c2cc30ea66cf0847ebbb7c4d6e0523370d941b841206d080626c3ea86c7746c2d2891d1dbe62c91e61d9dda2d234bd1a0598224067fb8fabe108b5c569eccb191afd7f5fc8bb9229c2848637747670'

main()