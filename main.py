"""NApp responsible to discover new switches and hosts."""
import struct

from kytos.core import KytosEvent, KytosNApp, log
from kytos.core.helpers import listen_to
from pyof.foundation.basic_types import DPID, UBInt16
from pyof.foundation.network_types import LLDP, Ethernet, EtherType
from pyof.v0x01.common.action import ActionOutput as AO10
from pyof.v0x01.controller2switch.packet_out import PacketOut as PO10
from pyof.v0x04.common.action import ActionOutput as AO13
from pyof.v0x04.controller2switch.packet_out import PacketOut as PO13

from . import constants, settings


class Main(KytosNApp):
    """Main OF_LLDP NApp Class."""

    def setup(self):
        """Initial setup this NApp to run in a loop."""
        self.execute_as_loop(settings.POLLING_TIME)

    def execute(self):
        """Send LLDP Packets every 'POLLING_TIME' seconds to all switches."""
        switches = list(self.controller.switches.values())
        for switch in switches:
            try:
                of_version = switch.connection.protocol.version
            except AttributeError:
                of_version = None

            if not (switch.is_connected() and of_version in [0x01, 0x04]):
                continue

            for interface in switch.interfaces.values():

                # Avoid ports with speed == 0
                if interface.port_number == 65534:
                    continue

                lldp = LLDP()
                lldp.chassis_id.sub_value = DPID(switch.dpid)
                lldp.port_id.sub_value = interface.port_number

                ethernet = Ethernet()
                ethernet.ether_type = EtherType.LLDP
                ethernet.source = interface.address
                ethernet.destination = constants.LLDP_MULTICAST_MAC
                ethernet.data = lldp.pack()

                packet_out = self.build_lldp_packet_out(of_version,
                                                        interface.port_number,
                                                        ethernet.pack())

                if packet_out is not None:
                    name = 'diraol/of_lldp.messages.out.ofpt_packet_out'
                    content = {'destination': switch.connection,
                               'message': packet_out}
                    event_out = KytosEvent(name=name, content=content)
                    self.controller.buffers.msg_out.put(event_out)

                    log.debug("Sending a LLDP PacketOut to the switch %s",
                              switch.dpid)

    @listen_to('kytos/of_core.v0x0[14].messages.in.ofpt_packet_in')
    def notify_uplink_detected(self, event):
        """Dispatch an KytosEvent to notify about a link between switches.

        Args:
            event (:class:`~kytos.core.events.KytosEvent`):
                Event with an LLDP packet as data.

        """
        ethernet = self.unpack_non_empty(Ethernet, event.message.data)
        if ethernet.ether_type == EtherType.LLDP:
            try:
                lldp = self.unpack_non_empty(LLDP, ethernet.data)
                dpid = self.unpack_non_empty(DPID, lldp.chassis_id.sub_value)
            except struct.error:
                #: If we have a LLDP packet but we cannot unpack it, or the
                #: unpacked packet does not contain the dpid attribute, then
                #: we are dealing with a LLDP generated by someone else. Thus
                #: this packet is not useful for us and we may just ignore it.
                return

            switch_a = event.source.switch
            port_a = event.message.in_port

            switch_b = self.controller.get_switch_by_dpid(dpid.value)
            port_b = self.unpack_non_empty(UBInt16, lldp.port_id.sub_value)

            name = 'diraol/of_lldp.switch.link'
            content = {'switch_a': {'id': switch_a.id, 'port': port_a},
                       'switch_b': {'id': switch_b.id, 'port': port_b}}

            event_out = KytosEvent(name=name, content=content)
            self.controller.buffers.app.put(event_out)

    def shutdown(self):
        """End of the application."""
        log.debug('Shutting down...')

    @staticmethod
    def build_lldp_packet_out(version, port_number, data):
        """Build a LLDP PacketOut message.

        Args:
            version (int): OpenFlow version
            port_number (int): Switch port number where the packet must be
                forwarded to.
            data (bytes): Binary data to be sent through the port.

        Returns:
            PacketOut message for the specific given OpenFlow version, if it
                is supported.
            None if the OpenFlow version is not supported.

        """
        if version == 0x01:
            action_output_class = AO10
            packet_out_class = PO10
        elif version == 0x04:
            action_output_class = AO13
            packet_out_class = PO13
        else:
            log.info('Openflow version %s is not yet supported.', version)
            return None

        output_action = action_output_class()
        output_action.port = port_number

        packet_out = packet_out_class()
        packet_out.data = data
        packet_out.actions.append(output_action)

        return packet_out

    @staticmethod
    def unpack_non_empty(desired_class, data):
        """Unpack data using an instance of desired_class.

        Args:
            desired_class (class): The class to be used to unpack data.
            data (bytes): bytes to be unpacked.

        Return:
            An instance of desired_class class with data unpacked into it.

        Raises:
            UnpackException if the unpack could not be performed.

        """
        obj = desired_class()

        if hasattr(data, 'value'):
            data = data.value

        obj.unpack(data)

        return obj
