# User Roles and Permissions System
# Project Bin - ระบบจัดการสิทธิ์การเข้าถึง

class UserRole:
    """ระบบจัดการสิทธิ์ผู้ใช้งาน"""
    
    # ระดับสิทธิ์ (จากต่ำไปสูง)
    ROLES = {
        'Member': 1,        # สมาชิกทั่วไป
        'Moderator': 2,     # ผู้ดูแลเนื้อหา (ร้านค้า)
        'Administrator': 3,  # ผู้ดูแลระบบ
        'Root Admin': 4     # สิทธิ์สูงสุด
    }
    
    @staticmethod
    def can_manage_orders(user_role):
        """ตรวจสอบสิทธิ์ในการจัดการคำสั่งซื้อ"""
        return user_role in ['Root Admin', 'Administrator', 'Moderator']
    
    @staticmethod
    def can_edit_orders(user_role):
        """ตรวจสอบสิทธิ์ในการแก้ไขคำสั่งซื้อ"""
        return user_role in ['Root Admin', 'Administrator', 'Moderator']
    
    @staticmethod
    def can_delete_orders(user_role):
        """ตรวจสอบสิทธิ์ในการลบคำสั่งซื้อ"""
        return user_role in ['Root Admin', 'Administrator', 'Moderator']
    
    @staticmethod
    def can_manage_categories(user_role):
        """ตรวจสอบสิทธิ์ในการจัดการหมวดหมู่"""
        return user_role in ['Root Admin', 'Administrator']
    
    @staticmethod
    def can_manage_products(user_role):
        """ตรวจสอบสิทธิ์ในการจัดการสินค้า"""
        return user_role in ['Root Admin', 'Administrator', 'Moderator']
    
    @staticmethod
    def can_manage_users(user_role):
        """ตรวจสอบสิทธิ์ในการจัดการผู้ใช้"""
        return user_role in ['Root Admin', 'Administrator']
    
    @staticmethod
    def can_access_member_data(user_role, target_user_id, current_user_id):
        """ตรวจสอบสิทธิ์ในการเข้าถึงข้อมูล Member"""
        if user_role in ['Root Admin', 'Administrator']:
            return True
        elif user_role == 'Moderator':
            # Moderator สามารถเข้าถึงได้เฉพาะ Member ที่ผูกกับตนเอง
            return target_user_id == current_user_id
        elif user_role == 'Member':
            # Member เข้าถึงได้เฉพาะข้อมูลตนเอง
            return target_user_id == current_user_id
        return False
    
    @staticmethod
    def can_update_disposed_quantity(user_role):
        """ตรวจสอบสิทธิ์ในการอัพเดทจำนวนที่ทิ้งแล้ว"""
        return user_role in ['Root Admin', 'Administrator', 'Moderator', 'Member']
    
    @staticmethod
    def get_role_level(role):
        """ได้ระดับสิทธิ์ของ role"""
        return UserRole.ROLES.get(role, 0)
    
    @staticmethod
    def is_higher_role(role1, role2):
        """ตรวจสอบว่า role1 มีสิทธิ์สูงกว่า role2 หรือไม่"""
        return UserRole.get_role_level(role1) > UserRole.get_role_level(role2)

class BarcodeManager:
    """ระบบจัดการ Barcode และการเชื่อมต่อกับเครื่องสแกน"""
    
    @staticmethod
    def validate_barcode_scan(barcode_id, quantity_to_dispose):
        """ตรวจสอบการสแกน barcode"""
        # ตรวจสอบว่า barcode มีอยู่ในระบบหรือไม่
        # ตรวจสอบจำนวนที่ทิ้งไม่เกินจำนวนที่ซื้อ
        pass
    
    @staticmethod
    def update_disposed_quantity(barcode_id, additional_quantity=1):
        """อัพเดทจำนวนที่ทิ้งแล้วจากการสแกน"""
        # เพิ่มจำนวนที่ทิ้งแล้วทีละ 1 หน่วยจากการสแกน
        pass
    
    @staticmethod
    def check_completion_status(barcode_id):
        """ตรวจสอบสถานะความสมบูรณ์ของการทิ้ง"""
        # ตรวจสอบว่าจำนวนที่ทิ้งเท่ากับจำนวนที่ซื้อหรือไม่
        pass
    
    @staticmethod
    def can_scan_barcode(barcode_id):
        """ตรวจสอบว่าสามารถสแกน barcode ได้หรือไม่"""
        # ตรวจสอบว่ายังสามารถทิ้งเพิ่มได้หรือไม่
        pass

class RegistrationManager:
    """ระบบจัดการการลงทะเบียน"""
    
    @staticmethod
    def create_viewer_account(user_data):
        """สร้างบัญชี Viewer"""
        # สร้างบัญชีทันทีโดยไม่ต้องรออนุมัติ
        pass
    
    @staticmethod
    def create_moderator_request(user_data):
        """สร้างคำขอลงทะเบียน Moderator"""
        # ส่งข้อมูลไปยัง pianlerdpringpror@gmail.com
        # เก็บข้อมูลไว้รออนุมัติ
        pass
     
    @staticmethod
    def send_moderator_approval_email(user_data):
        """ส่งอีเมลขออนุมัติ Moderator"""
        email_content = f"""
        คำขอลงทะเบียน Moderator ใหม่:
        
        ชื่อ-นามสกุล: {user_data.get('fullname')}
        อีเมล: {user_data.get('email')}
        ชื่อร้านค้า: {user_data.get('shop_name')}
        เบอร์โทรศัพท์: {user_data.get('phone')}
        ที่อยู่: {user_data.get('address')}
        ประเภทธุรกิจ: {user_data.get('business_type')}
        """
        # ส่งอีเมลไปยัง pianlerdpringpror@gmail.com
        pass

# ตัวอย่างการใช้งาน
def check_order_permissions(user_role, action):
    """ตรวจสอบสิทธิ์การทำงานกับคำสั่งซื้อ"""
    permissions = {
        'view': True,  # ทุกคนดูได้
        'create': UserRole.can_manage_orders(user_role),
        'edit': UserRole.can_edit_orders(user_role),
        'delete': UserRole.can_delete_orders(user_role),
        'update_disposed': UserRole.can_update_disposed_quantity(user_role)
    }
    return permissions.get(action, False)