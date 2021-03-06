from blockmesh.block import *
from enum import Enum

HEAD_FILE = "HEAD"


def mkdir(path_to_dir):
    path_to_dir = os.path.abspath(path_to_dir)
    if not path_to_dir:
        raise NotADirectoryError(f"Not a dir: {path_to_dir}")
    if not os.path.isdir(path_to_dir):
        os.makedirs(path_to_dir)
    return path_to_dir


class Mod(Enum):
    """
    Режимы работы узлов и протокола блокмеш
    """
    Classic = 1
    Modified = 2


class Storage:
    """
    Класс реализующий функционал узлов-хранилищ blockmesh сети
    """

    def __init__(self, mod: Mod, path_to_dir: str, timeserver):
        """
        :param mod: режим работы
        :param path_to_dir: путь к дирректории в которой будут храниться блоки этого узла
        :type timeserver:
        """
        if mod == Mod.Classic:
            self.queue = set()  # []
            self.shared_blocks = []
        elif mod == Mod.Modified:
            self.queue = {}
            self.shared_blocks = {}
        else:
            raise ValueError(f"Unknown mod: {mod.name}")
        self.mod = mod
        self.path_to_dir = mkdir(path_to_dir)
        self.stg_list = []    # list of StgNodes
        self.user_map = {}    # addr and its UsrNode
        self.block_mesh = {}  # addr and its head
        self.block_count = 1  # genesis at least
        self.available = True
        self.timeserver = timeserver

    def save(self):
        """
        Запись состояния узла-хранилища в HEAD-файл
        """
        with open(os.path.join(self.path_to_dir, HEAD_FILE), "w") as file:
            json.dump({'mod': self.mod.name,
                       'heads': self.block_mesh,
                       'available': self.available,
                       'queue': [b.dumps() for b in self.queue] if self.mod == Mod.Classic else
                       {b.dumps(): c for b, c in self.queue.items()},
                       'blocks': self.block_count}, file)

    @staticmethod
    def load(path_to_dir, timeserver, stg_list=None, usr_map=None):
        """
        Восстановление состояния узла-хранилища из файла
        :param path_to_dir: путь к дирректории
        :param timeserver:
        :param usr_map: словарь {адрес узла-участника: узел участник}
        :param stg_list: список узлов хранилищ
        :return: StgNode
        """
        path_to_dir = os.path.abspath(path_to_dir)
        if path_to_dir is None:
            raise RuntimeError(f"Could not load StgNode: {path_to_dir} does not exist")
        with open(os.path.join(path_to_dir, HEAD_FILE), "r") as file:
            data = json.load(file)
            mod = Mod[data['mod']]
            stg = Storage(mod, path_to_dir, timeserver)
            stg.block_mesh = data['heads']
            if mod == Mod.Classic:
                stg.queue = set([Block.loads(blocks) for blocks in data['queue']])
            else:
                stg.queue = {Block.loads(blocks): data['queue'][blocks] for blocks in data['queue']}
            stg.block_count = data['blocks']
            bc = stg.index_blocks()
            if len(bc) != stg.block_count:
                print(f"Storage broken! IndexBC: {len(bc)} != SelfBC :{stg.block_count}")
            stg.available = data['available']
            stg.user_map = usr_map if usr_map else {}
            stg.stg_list = []
            if stg_list:
                stg.stg_list = stg_list
                for other_stg in stg_list:
                    other_stg.stg_list.append(stg)
            return stg

    def get_time(self):
        """
        :return: Серверное время
        """
        return self.timeserver.time

    def join_bm(self, other_stg):
        """
        Присоединить узел к блокмешу
        :param other_stg: Узел-хранилище
        """
        if self.stg_list:
            raise RuntimeError(f"Already in blockmesh: {self.stg_list}")
        self.stg_list.append(other_stg)
        self.stg_list.extend(other_stg.stg_list)
        for stg in self.stg_list:
            stg.stg_list.append(self)
        self.refresh_blocks()

    def global_bm_participants(self):
        """
        :return: Количество участников сети блокмеш
        """
        return len(self.block_mesh)

    def local_bm_participants(self):
        """
        :return: Количество локальных узлов-участников сети блокмеш
        """
        return len(self.user_map)

    def queue_len(self):
        """
        :return: Количество блоков в очереди на добавление в блокмеш
        """
        return len(self.queue) if self.mod == Mod.Classic else sum(self.queue.values())

    def disable(self):
        """
        Деактивировать работу узла
        """
        if self.available:
            self.available = False

    def enable(self):
        """
        Активировать работу узла
        """
        if not self.available:
            self.refresh_blocks()
            self.available = True

    def refresh_blocks(self):
        """
        Проверка и обновление блоков блокмеш
        """
        self_index = self.index_blocks()
        other_index = {}
        other_stg = None
        for stg in self.stg_list:
            if stg.available:
                other_index = stg.index_blocks()
                other_stg = stg
                break
        if other_stg is None or not other_index:
            raise Warning(f"INFO: Unable to refresh blocks ->"
                          f" no available stg: {self.stg_list}")
        if self_index == other_index:
            return
        check_index = other_index.copy()
        other_index -= self_index
        for index in other_index:
            b = other_stg.load_block(index)
            b.save(self.path_to_dir)
        self.block_mesh = other_stg.block_mesh.copy()
        self_index = self.index_blocks()
        if check_index != self_index:
            self.available = False
            raise RuntimeError(f"Local blockmesh totally broken:\n"
                               f"Self  index: {self_index}\n"
                               f"Check index: {check_index}")
        self.block_count = len(self_index)

    def index_blocks(self):
        """
        :return: set из хэшей всех блоков в блокмеше
        """
        index = {GENESIS_BLOCK}
        queue = list(set(self.block_mesh.values()))
        while queue:
            block_id = queue.pop(0)
            if block_id is None or block_id in index:
                continue
            block = self.load_block(block_id)
            queue.extend(list(set(block.parents.values())))
            index.add(block_id)
        return index

    def load_block(self, block_id):
        return Block.load(os.path.join(self.path_to_dir, block_id))

    def add_new_block(self, block: Block):
        """
        Принятие блока на обработку с целью добавить в блокмеш
        :param block: блок блокмеша
        """
        if not self.available:
            # Ошибка? или просто не принимать участие?!
            raise RuntimeError(f"Stg is disabled: {self}")
        if self.mod == Mod.Classic:
            self.queue.add(block)
        elif self.mod == Mod.Modified:
            self.queue[block] = 1 if block not in self.queue else \
                                self.queue[block] + 1
        else:
            raise RuntimeError("WTF - add new block")

    def connect_user(self, user):
        """
        Добавить нового пользователя. Подключить к узлу-хранилищу
        :param user: UsrNode
        """
        if user.addr not in self.user_map:
            self.user_map[user.addr] = user
        if self.user_map[user.addr].head != user.head:
            raise RuntimeError(f"Usr HEAD: {self.user_map[user.addr].head} != new Usr HEAD: {user.head}")
        if user.addr not in self.block_mesh:
            self.block_mesh[user.addr] = user.head = GENESIS_BLOCK
            for stg in self.stg_list:
                stg.block_mesh[user.addr] = GENESIS_BLOCK
        else:
            user.head = self.block_mesh[user.addr]
        user.inited = True

    def disconnect_user(self, user):
        """
        Отключить пользователя от узла-хранилища
        :param user: UsrNode
        """
        if user.addr not in self.user_map:
            raise RuntimeError(f"WTF. {user.addr} not in user map")
        self.user_map.pop(user.addr)

    @staticmethod
    def check_block(block: Block):
        """
        WTF должна быть проверка транзакции в блоке
        :param block: Block
        :return: Bool
        """
        return True if block.approved is not False else False

    def perform_step_1(self):
        """
        Шаг 1 - "рассылка" блока для консенсуса
        """
        if not self.available:
            # print(f"Stg is disabled: {self.path_to_dir}. Unable to perform step 1.")
            return
        if self.mod == Mod.Classic:
            self.__perform_step_1()
        elif self.mod == Mod.Modified:
            self.__perform_step_1_mod()
        else:
            raise RuntimeError("WTF - perform step 1")

    def perform_step_2(self, i=0):
        """
        Шаг 2 - Конфликтующие транзакции откладываются, валидные внедряются в блокмеш
        """
        if not self.available:
            # print(f"Stg is disabled: {self.path_to_dir}. Unable to perform step 2.")
            return
        if self.mod == Mod.Classic:
            self.__perform_step_2(i)
        elif self.mod == Mod.Modified:
            self.__perform_step_2_mod(i)
        else:
            raise RuntimeError("WTF - perform step 2")

    def get_users(self, users):
        """
        Запрос на взаимодействие с другими узлами-участниками
        :param users: список адресов
        :return: список UsrNode
        """
        if not self.available:
            raise RuntimeError(f"Stg is disabled: {self.path_to_dir}")
        if not users:
            raise RuntimeError(f"Unable to get users - empty")
        return [self.__request_user(user) for user in users]

    def __perform_step_1(self):
        for block in self.queue.copy():
            if self.check_block(block) is False:
                block.approved = False
                self.user_map[block.sender()].receive_from_stg(block)
                self.queue.pop(block)
                continue
            block.approved = True
            self.__block_sending(block)

    def __perform_step_1_mod(self):
        to_send = len(self.user_map)
        for block, count in self.queue.items():
            if to_send == 0:
                break
            if self.check_block(block) is False:
                block.approved = False
                self.user_map[block.sender()].receive_from_stg(block)
                self.queue.pop(block)
                continue
            block.approved = True
            self.__block_sending(block, count)
            to_send -= 1

    def __block_sending(self, block, count=None):
        if self.mod == Mod.Classic:
            self.shared_blocks.append(block)
            for stg in self.stg_list:
                if stg.available:
                    stg.shared_blocks.append(block.copy())
        elif self.mod == Mod.Modified:
            # возможны ошибки
            if block in self.shared_blocks:
                self.shared_blocks[block] += count
            else:
                self.shared_blocks[block] = count
            for stg in self.stg_list:
                if stg.available:
                    if block in stg.shared_blocks:
                        stg.shared_blocks[block.copy()] += count
                    else:
                        stg.shared_blocks[block.copy()] = count
        else:
            raise RuntimeError("WTF - send block")

    def __perform_step_2(self, i):
        if not self.shared_blocks:
            return
        self.shared_blocks.sort(key=lambda b: b.timestamp)
        participants = set()
        while self.shared_blocks:
            block = self.shared_blocks.pop(0)
            cblock = block.copy()
            if not self.__check_and_insert(block, participants, i):
                continue
            if self.queue and cblock in self.queue:
                self.queue.remove(cblock)

    def __perform_step_2_mod(self, i):
        if not self.shared_blocks:
            return
        blocks = list(self.shared_blocks.keys())
        blocks.sort(key=lambda b: b.timestamp)
        participants = set()
        while blocks:
            block = blocks.pop(0)
            count = self.shared_blocks.pop(block)
            if len(block.participants()) != count:
                continue
            cblock = block.copy()
            if not self.__check_and_insert(block, participants, i):
                continue
            if self.queue and cblock in self.queue:
                self.queue.pop(cblock)

    def __check_and_insert(self, block, participants, i):
        # проверка
        users = block.participants()
        for user in users:
            if user in participants:
                return False
        participants.update(users)
        # внедрение в блокмеш
        block.set_parents({usr: self.block_mesh[usr] for usr in users})
        block.on_iter = i
        fname = block.save(self.path_to_dir)
        for user in users:
            self.block_mesh[user] = fname
            if user in self.user_map:
                self.user_map[user].receive_from_stg(block)
        self.block_count += 1
        return True

    def __request_user(self, user):
        if user in self.user_map:
            return self.user_map[user]
        flag = False
        for stg in self.stg_list:
            if not stg.available:
                # print(f"INFO: {stg.path_to_dir} is unavailable cant request user")
                flag = True
                continue
            if user in stg.user_map:
                return stg.user_map[user]
        if flag:
            return None
        raise RuntimeError(f"There is no such user: {user}")


class User:
    """
    Класс реализующий функционал узлов-участников blockmesh сети
    """

    def __init__(self, mod: Mod, path_to_dir: str, addr: str, sign: str, stg: Storage = None, head: str = None):
        """
        :param mod: режим работы
        :param path_to_dir: путь к дирректории в которой будут храниться блоки этого узла
        :param addr: идентификатор узла
        :param sign: подпись узла
        :param stg: узел-хранилище через который будет обеспечиваться взаимодействие с другими участниками
        """
        if mod == Mod.Classic:
            self.generation_allowed = None
        elif mod == Mod.Modified:
            self.generation_allowed = True
        else:
            raise RuntimeError(f"Unknown mod: {mod.name}")
        self.mod = mod
        self.path_to_dir = mkdir(path_to_dir)
        self.addr = addr
        self.sign = sign
        self.stg = stg
        self.inited = False
        self.head = head
        self.block_count = 0
        stg.connect_user(self)

    def save(self):
        """
        Сохранить состояние узла в HEAD-файл
        """
        if not self.inited or not self.head:
            raise RuntimeError(f"Unable to save {self.addr} UsrNode: "
                               f"not inited [{self.inited}] or has no head [{self.head}]")
        with open(os.path.join(self.path_to_dir, HEAD_FILE), "w") as f:
            json.dump({"head": self.head, "addr": self.addr, "sign": self.sign, "mod": self.mod.name}, f)

    @staticmethod
    def load(path_to_dir, stg: Storage):
        """
        Восстановление состояния узла-участника из файла
        :param path_to_dir: путь к дирректории
        :param stg: Узел-хранилище
        :return: UsrNode
        """
        path_to_dir = os.path.abspath(path_to_dir)
        if path_to_dir is None:
            raise RuntimeError(f"Could not load UsrNode: {path_to_dir} does not exist")
        with open(os.path.join(path_to_dir, HEAD_FILE), "r") as f:
            data = json.load(f)
            node = User(Mod[data['mod']], path_to_dir, data['addr'], data['sign'], stg, data['head'])
            node.block_count = len(node.index_blocks())
            return node

    def change_stg(self, new_stg: Storage):
        """
        Сменить узе-хранилище
        :param new_stg: узел-хранилище
        """
        if not new_stg.available:
            raise RuntimeError(f"New storage is not available: {new_stg.path_to_dir}")
        self.stg.disconnect_user(self)
        self.stg = new_stg
        self.stg.connect_user(self)

    def sign_tx(self, tx: Transaction):
        """
        Подписание транзакции
        :param tx: Транзакция
        :return: хэш родительского блока
        """
        if not self.inited:
            raise RuntimeError("UsrNode not inited in his StgNode")
        tx.sign(self.addr, self.sign)
        return self.head

    def receive_from_stg(self, block: Block):
        """
        Продолжение второго этапа работы blockmesh - внедрение блока
        :param block: блок для внедрения в локальную цепочку
        """
        if block.approved is True and self.check_chain(block):
            self.head = block.save(self.path_to_dir)
            if self.mod == Mod.Modified and self.addr == block.sender():
                self.generation_allowed = True
            self.block_count += 1

    def index_blocks(self):
        index = {GENESIS_BLOCK}
        parent_hash = self.head
        while parent_hash != GENESIS_BLOCK:
            index.add(parent_hash)
            block = Block.load(os.path.join(self.path_to_dir, parent_hash))
            parent_hash = block.parents[self.addr]
        return index

    def check_chain(self, block: Block):
        """
        Проверка локальной цепочки блокмеш
        :param block: блок внедряемый в локальную цепочку
        :return: Bool
        """
        if block.parents[self.addr] != self.head:
            raise RuntimeError(f"Check chain error:[ Block parent hash: {block.parents[self.addr]} "
                               f"!= Usr parent hash: {self.head} ]")
        parent_hash = self.head
        while parent_hash != GENESIS_BLOCK:
            try:
                read_block = Block.load(os.path.join(self.path_to_dir, parent_hash))
            except Exception as e:
                # print("INFO:", e)
                return False
            parent_hash = read_block.parents[self.addr]
        return True

    def perform(self, recv_addr: list, data: dict = None):
        """
        Первый этап работы blockmesh - взаимодействие
        :param recv_addr: список получателей транзакции
        :param data: данные
        """
        if not self.inited:
            raise RuntimeError("UsrNode not inited in his StgNode")
        if not self.stg.available:
            # print(f"INFO: {self.stg.path_to_dir} is not available! Change stg!")
            return
        if self.mod == Mod.Classic:
            self.__perform(recv_addr, data)
        elif self.mod == Mod.Modified:
            if not self.generation_allowed:
                return
            res = self.__perform(recv_addr, data)
            if res:
                block, receivers = res
                for receiver in receivers:
                    receiver.stg.add_new_block(block)
                self.generation_allowed = False

    def __perform(self, recv_addr: list, data: dict = None):
        tx = self.__create_tx(recv_addr, data)
        receivers = self.stg.get_users(recv_addr)
        for receiver in receivers:
            if receiver is None:
                print(f"INFO: One of receivers potential unavailable: {recv_addr} -> {receivers}")
                return None
            receiver.sign_tx(tx)
            if receiver.stg.available is False:
                raise RuntimeError(f"{receiver.addr} is not available!")
        block = self.__create_block(tx)
        self.stg.add_new_block(block)
        return block, receivers

    def __create_tx(self, receivers: list, data: dict = None):
        return Transaction(sender_addr=self.addr, sender_sign=self.sign,
                           receivers=receivers, data=data if data else {})

    def __create_block(self, tx: Transaction):
        if tx.sender != self.addr:
            raise RuntimeError(f"TxSenderAddr {tx.sender} != UsrAddr {self.addr}")
        return Block(tx, self.stg.get_time())
