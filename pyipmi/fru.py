#
# pyimpi
#
# author: Heiko Thiery <heiko.thiery@kontron.com>
# author: Michael Walle <michael.walle@kontron.com>
#

import array
import codecs
import datetime

from pyipmi.errors import DecodingError, CompletionCodeError
from pyipmi.msgs import create_request_by_name
from pyipmi.msgs import constants
from pyipmi.utils import check_completion_code, bcd_search, chunks

codecs.register(bcd_search)

class Fru:
    def __init__(self):
        self.write_length = 16

    def get_fru_inventory_area_info(self, fru_id=0):
        req = create_request_by_name('GetFruInventoryAreaInfo')
        req.fru_id = fru_id
        rsp = self.send_message(req)
        check_completion_code(rsp.completion_code)
        return rsp.area_size

    def write_fru_data(self, data, offset=0, fru_id=0):
        req = create_request_by_name('WriteFruData')
        req.fru_id = fru_id

        for chunk in chunks(data, self.write_length):
            req.offset = offset
            req.data = chunk
            rsp = self.send_message(req)
            check_completion_code(rsp.completion_code)
            offset += len(chunk)

    def read_fru_data(self, offset=None, count=None, fru_id=0):
        off = 0
        area_size = 0
        req_size = 32
        data = array.array('B')

        # first check for maximum area size
        if offset is None:
            area_size = self.get_fru_inventory_area_info(fru_id)
            off = 0
        else:
            area_size = offset + count
            off = offset

        while off < area_size:
            if (off + req_size) > area_size:
                req_size = area_size - off

            req = create_request_by_name('ReadFruData')
            req.fru_id = fru_id
            req.offset = off
            req.count = req_size
            try:
                rsp = self.send_message(req)
                check_completion_code(rsp.completion_code)
            except CompletionCodeError, e:
                if e.cc == constants.CC_CANT_RET_NUM_REQ_BYTES:
                    req_size -= 2
                    continue
                if e.cc == constants.CC_REQ_DATA_FIELD_EXCEED:
                    req_size -= 2
                    continue
                if e.cc == constants.CC_PARAM_OUT_OF_RANGE:
                    req_size -= 2
                    continue
                else:
                    raise

            data.extend(rsp.data)
            off += rsp.count

        return data.tostring()

    def get_fru_inventory(self, fru_id=0):
        return FruInventory(self.read_fru_data(fru_id=fru_id))

class FruDataField:
    TYPE_BINARY = 0
    TYPE_BCD_PLUS = 1
    TYPE_6BIT_ASCII = 2
    TYPE_ASCII_OR_UTF16 = 3

    def __init__(self, data=None, offset=0, force_lang_english=False):
        if data:
            self.from_data(data, offset, force_lang_english)

    def __str__(self):
        if self.type is FruDataField.TYPE_BINARY:
            return ' '.join('%02x' % ord(b) for b in self.value)
        else:
            return self.value.replace('\x00', '')

    def from_data(self, data, offset=0, force_lang_english=False):
        self.type = ord(data[offset]) >> 6 & 0x3
        self.length = ord(data[offset]) & 0x3f

        self.raw = data[offset+1:offset+1+self.length]

        if type == self.TYPE_BCD_PLUS:
            value = self.raw.decode('bcd+')
        elif type == self.TYPE_6BIT_ASCII:
            value = self.raw.decode('6bitascii')
        else:
            value = self.raw

        self.value = value

class InventoryCommonHeader:
    def __init__(self, data=None):
        if data:
            self.from_data(data)

    def from_data(self, data):
        if len(data) != 8:
            raise DecodingError('InventoryCommonHeader length != 8')
        self.format_version = ord(data[0]) & 0x0f
        self.internal_use_area_offset = ord(data[1]) * 8 or None
        self.chassis_info_area_offset = ord(data[2]) * 8 or None
        self.board_info_area_offset = ord(data[3]) * 8 or None
        self.product_info_area_offset = ord(data[4]) * 8 or None
        self.multirecord_area_offset = ord(data[5]) * 8 or None
        if sum([ord(c) for c in data]) % 256 != 0:
            raise DecodingError('InventoryCommonHeader checksum failed')


class CommonInfoArea:
    def __init__(self, data=None):
        if data:
            self.from_data(data)

    def from_data(self, data):
        self.format_version = ord(data[0]) & 0x0f
        if self.format_version != 1:
            raise DecodingError('unsupported format version (%d)' %
                    self.format_version)
        self.length = ord(data[1]) * 8
        if sum([ord(c) for c in data[:self.length]]) % 256 != 0:
            raise DecodingError('checksum failed')

class InventoryChassisInfoArea(CommonInfoArea):
    TYPE_OTHER = 1
    TYPE_UNKNOWN = 2
    TYPE_DESKTOP = 3
    TYPE_LOW_PROFILE_DESKTOP = 4
    TYPE_PIZZA_BOX = 5
    TYPE_MINI_TOWER = 6
    TYPE_TOWER = 7
    TYPE_PORTABLE = 8
    TYPE_LAPTOP = 9
    TYPE_NOTEBOOK = 10
    TYPE_HAND_HELD = 11
    TYPE_DOCKING_STATION = 12
    TYPE_ALL_IN_ONE = 13
    TYPE_SUB_NOTEBOOK = 14
    TYPE_SPACE_SAVING = 15
    TYPE_LUNCH_BOX = 16
    TYPE_MAIN_SERVER_CHASSIS = 17
    TYPE_EXPANSION_CHASSIS = 18
    TYPE_SUB_CHASSIS = 19
    TYPE_BUS_EXPANSION_CHASSIS = 20
    TYPE_PERIPHERAL_CHASSIS = 21
    TYPE_RAID_CHASSIS = 22
    TYPE_RACK_MOUNT_CHASSIS = 23

    def from_data(self, data):
        CommonInfoArea.from_data(self, data)
        self.type = ord(data[2])
        offset = 3
        self.part_number = FruDataField(data, offset)
        offset += self.part_number.length+1
        self.serial_number = FruDataField(data, offset, True)
        offset += self.serial_number.length+1
        self.custom_chassis_info = list()
        while ord(data[offset]) != 0xc1:
            field = FruDataField(data, offset)
            self.custom_chassis_info.append(field)
            offset += field.length+1

class InventoryBoardInfoArea(CommonInfoArea):
    def from_data(self, data):
        CommonInfoArea.from_data(self, data)
        self.language_code = ord(data[2])
        minutes = ord(data[5]) << 16 | ord(data[4]) << 8 | ord(data[3])
        self.mfg_date = (datetime.datetime(1996, 1, 1)
                + datetime.timedelta(minutes=minutes))
        offset = 6
        self.manufacturer = FruDataField(data, offset)
        offset += self.manufacturer.length+1
        self.product_name = FruDataField(data, offset)
        offset += self.product_name.length+1
        self.serial_number = FruDataField(data, offset, True)
        offset += self.serial_number.length+1
        self.part_number = FruDataField(data, offset)
        offset += self.part_number.length+1
        self.fru_file_id = FruDataField(data, offset, True)
        offset += self.fru_file_id.length+1
        self.custom_mfg_info = list()
        while ord(data[offset]) != 0xc1:
            field = FruDataField(data, offset)
            self.custom_mfg_info.append(field)
            offset += field.length+1

class InventoryProductInfoArea(CommonInfoArea):
    def from_data(self, data):
        CommonInfoArea.from_data(self, data)
        self.language_code = ord(data[2])
        offset = 3
        self.manufacturer = FruDataField(data, offset)
        offset += self.manufacturer.length+1
        self.name = FruDataField(data, offset)
        offset += self.name.length+1
        self.part_number = FruDataField(data, offset)
        offset += self.part_number.length+1
        self.version = FruDataField(data, offset)
        offset += self.version.length+1
        self.serial_number = FruDataField(data, offset, True)
        offset += self.serial_number.length+1
        self.asset_tag = FruDataField(data, offset)
        offset += self.asset_tag.length+1
        self.fru_file_id = FruDataField(data, offset, True)
        offset += self.fru_file_id.length+1
        self.custom_mfg_info = list()
        while ord(data[offset]) != 0xc1:
            field = FruDataField(data, offset)
            self.custom_mfg_info.append(field)
            offset += field.length+1


class FruDataMultiRecord:
    TYPE_POWER_SUPPLY_INFORMATION = 0
    TYPE_DC_OUTPUT = 1
    TYPE_DC_LOAD = 2
    TYPE_MANAGEMENT_ACCESS_RECORD = 3
    TYPE_BASE_COMPATIBILITY_RECORD = 4
    TYPE_EXTENDED_COMPATIBILITY_RECORD = 5
    TYPE_OEM = range(0x0c, 0x100)
    TYPE_OEM_PICMG = 0xc0

    def __init__(self, data):
        if data:
            self.from_data(data)

    def __str__(self):
        return '%02x: %s' % (self.type,
                ' '.join('%02x' % ord(b) for b in self.raw))

    def from_data(self, data):
        if len(data) < 5:
            raise DecodingError('data too short')
        self.record_type_id = ord(data[0])
        self.format_version = ord(data[1]) & 0x0f
        self.end_of_list = bool(ord(data[1]) & 0x80)
        self.length = ord(data[2])
        if sum([ord(c) for c in data[:5]]) % 256 != 0:
            raise DecodingError('FruDataMultiRecord header checksum failed')
        self.raw = data[5:5+self.length]
        if (sum([ord(c) for c in self.raw]) + ord(data[3])) % 256 != 0:
            raise DecodingError('FruDataMultiRecord record checksum failed')

    @staticmethod
    def create_from_record_id(data):
        if ord(data[0]) == FruDataMultiRecord.TYPE_OEM_PICMG:
            return FruPicmgRecord.create_from_record_id(data)
        else:
            return FruDataUnknown(data)


class FruDataUnknown(FruDataMultiRecord):
    """This class is used to indicate undecoded picmg record."""
    pass

class FruPicmgRecord(FruDataMultiRecord):
    PICMG_RECORD_ID_BACKPLANE_PTP_CONNECTIVITY = 0x04
    PICMG_RECORD_ID_ADDRESS_TABLE = 0x10
    PICMG_RECORD_ID_SHELF_POWER_DISTRIBUTION = 0x11
    PICMG_RECORD_ID_SHMC_ACTIVATION_MANAGEMENT = 0x12
    PICMG_RECORD_ID_SHMC_IP_CONNECTION = 0x13
    PICMG_RECORD_ID_BOARD_PTP_CONNECTIVITY = 0x14
    PICMG_RECORD_ID_RADIAL_IPMB0_LINK_MAPPING = 0x15
    PICMG_RECORD_ID_MODULE_CURRENT_REQUIREMENTS = 0x16
    PICMG_RECORD_ID_CARRIER_ACTIVATION_MANAGEMENT = 0x17
    PICMG_RECORD_ID_CARRIER_PTP_CONNECTIVITY = 0x18
    PICMG_RECORD_ID_AMC_PTP_CONNECTIVITY = 0x19
    PICMG_RECORD_ID_CARRIER_INFORMATION = 0x1a
    PICMG_RECORD_ID_MTCA_FRU_INFORMATION_PARTITION = 0x20
    PICMG_RECORD_ID_MTCA_CARRIER_MANAGER_IP_LINK = 0x21
    PICMG_RECORD_ID_MTCA_CARRIER_INFORMATION = 0x22
    PICMG_RECORD_ID_MTCA_SHELF_INFORMATION = 0x23
    PICMG_RECORD_ID_MTCA_SHELF_MANAGER_IP_LINK = 0x24
    PICMG_RECORD_ID_MTCA_CARRIER_POWER_POLICY = 0x25
    PICMG_RECORD_ID_MTCA_CARRIER_ACTIVATION_AND_POWER = 0x26
    PICMG_RECORD_ID_MTCA_POWER_MODULE_CAPABILITY = 0x27
    PICMG_RECORD_ID_MTCA_FAN_GEOGRAPHY = 0x28
    PICMG_RECORD_ID_OEM_MODULE_DESCRIPTION = 0x29
    PICMG_RECORD_ID_CARRIER_CLOCK_PTP_CONNECTIVITY = 0x2C
    PICMG_RECORD_ID_CLOCK_CONFIGURATION = 0x2d
    PICMG_RECORD_ID_ZONE_3_INTERFACE_COMPATIBILITY = 0x30
    PICMG_RECORD_ID_CARRIER_BUSED_CONNECTIVITY = 0x31
    PICMG_RECORD_ID_ZONE_3_INTERFACE_DOCUMENTATION = 0x32

    def __init__(self, data):
        FruDataMultiRecord.__init__(self, data)

    @staticmethod
    def create_from_record_id(data):
        picmg_record = FruPicmgRecord(data)
        if picmg_record.picmg_record_type_id ==\
            FruPicmgRecord.PICMG_RECORD_ID_MTCA_POWER_MODULE_CAPABILITY:
            return FruPicmgPowerModuleCapabilityRecord(data)
        else:
            return FruPicmgRecord(data)

    def from_data(self, data):
        if len(data) < 10:
            raise DecodingError('data too short')
        FruDataMultiRecord.from_data(self, data)
        self.manufacturer_id = ord(data[5])|ord(data[6])<<8|ord(data[7])<<16
        self.picmg_record_type_id = ord(data[8])
        self.format_version = ord(data[9])


class FruPicmgPowerModuleCapabilityRecord(FruPicmgRecord):
    def from_data(self, data):
        if len(data) < 12:
            raise DecodingError('data too short')
        FruPicmgRecord.from_data(self,data)
        maximum_current_output = ord(data[10])|ord(data[11])<<8
        self.maximum_current_output = float(maximum_current_output/10)


class InventoryMultiRecordArea:
    def __init__(self, data):
        if data:
            self.from_data(data)

    def from_data(self, data):
        self.records = list()
        offset = 0
        while True:
            record = FruDataMultiRecord.create_from_record_id(data[offset:])
            self.records.append(record)
            offset += record.length+5
            if record.end_of_list:
                break


class FruInventory:
    def __init__(self, data=None):
        self.chassis_info_area = None
        self.board_info_area = None
        self.product_info_area = None

        if data:
            self.from_data(data)

    def from_data(self, data):
        self.raw = data
        self.common_header = InventoryCommonHeader(data[:8])

        if self.common_header.chassis_info_area_offset:
            self.chassis_info_area = InventoryChassisInfoArea(
                    data[self.common_header.chassis_info_area_offset:])

        if self.common_header.board_info_area_offset:
            self.board_info_area = InventoryBoardInfoArea(
                    data[self.common_header.board_info_area_offset:])

        if self.common_header.product_info_area_offset:
            self.product_info_area = InventoryProductInfoArea(
                    data[self.common_header.product_info_area_offset:])

        if self.common_header.multirecord_area_offset:
            self.multirecord_area = InventoryMultiRecordArea(
                    data[self.common_header.multirecord_area_offset:])
