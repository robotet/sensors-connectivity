import json
import select
import threading
import socket
from queue import LifoQueue
from stations import IStation, StationData, Measurement
from .comstation import BROADCASTER_VERSION


class ReadingThread(threading.Thread):
    MAX_CONNECTIONS = 10
    INPUTS = list()
    OUTPUTS = list()

    def __init__(self, address: str, q: LifoQueue):
        super().__init__()

        self.buffer = bytearray()
        self.q = q
        self.server_address = self._extract_ip_and_port(address)

    def _extract_ip_and_port(self, address: str) -> tuple:
        # assume the address looks like IP:PORT where IP is xx.xx.xx.xx
        splitted = address.split(":")
        return splitted[0], int(splitted[1])

    def _get_non_blocking_server_socket(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setblocking(False)
        server.bind(self.server_address)
        server.listen(self.MAX_CONNECTIONS)

        return server

    def handle_readables(self, readables, server):
        for resource in readables:
            if resource is server:
                connection, client_address = resource.accept()
                connection.setblocking(0)
                self.INPUTS.append(connection)
                rospy.loginfo("new connection from {address}".format(address=client_address))
            else:
                data = ""
                try:
                    data = resource.recv(1024)
                except ConnectionResetError:
                    pass

                if data:
                    rospy.loginfo("getting data: {data}".format(data=str(data)))
                    self.buffer.extend(data)

                    while len(self.buffer) > 1:
                        if b'\n' in self.buffer:
                            index = self.buffer.find(b'\n')
                            line = self.buffer[:index]
                            self.q.put(line.decode("utf-8", "backslashreplace"))
                            self.buffer = self.buffer[index + 1:]

                    if resource not in self.OUTPUTS:
                        self.OUTPUTS.append(resource)
                else:
                    self.clear_resource(resource)

    def clear_resource(self, resource):
        if resource in self.OUTPUTS:
            self.OUTPUTS.remove(resource)
        if resource in self.INPUTS:
            self.INPUTS.remove(resource)
        resource.close()

        rospy.loginfo('closing connection ' + str(resource))

    def run(self):
        server_socket = self._get_non_blocking_server_socket()
        self.INPUTS.append(server_socket)

        rospy.loginfo("Server is running...")

        try:
            while self.INPUTS:
                readables, writables, exceptional = select.select(self.INPUTS, self.OUTPUTS, self.INPUTS)
                self.handle_readables(readables, server_socket)
        except KeyboardInterrupt:
            self.clear_resource(server_socket)
            rospy.loginfo("Server stopped!")


class TCPStation(IStation):
    def __init__(self, config: dict):
        super().__init__(config)
        self.version = f"airalab-rpi-broadcaster-{BROADCASTER_VERSION}"

        self.q = LifoQueue()
        self.server = ReadingThread(self.config["tcpstation"]["address"], self.q)
        self.server.start()

    def __str__(self):
        return f"{{Version: {self.version}, Start: {self.start_time}, MAC: {self.mac_address}}}"

    def get_data(self) -> StationData:
        if self.q.empty():
            meas = Measurement()
        else:
            values = json.loads(self.q.get(timeout=3))
            meas = Measurement(values["pm25"], values["pm10"])

        return StationData(
            self.version,
            self.mac_address,
            time.time() - self.start_time,
            meas
        )