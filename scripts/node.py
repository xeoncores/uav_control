#!/usr/bin/env python
from __future__ import print_function, division, with_statement
import rospy
import tf
import dynamic_reconfigure.client
import numpy as np
from geometry_msgs.msg import PoseStamped, Point
from sensor_msgs.msg import Imu
from controller import *
import time
from i2c_cython.hw_interface import pyMotor
# import roslib
# roslib.load_manifest("estimation")
# from estimation import ukf_uav
# import ukf_uav
# from filterpy.kalman import UKF, JulierSigmaPoints
from numpy.linalg import inv
from uav_control.msg import states
from uav_control.msg import trajectory
from dynamic_reconfigure.server import Server
from uav_control.cfg import gainsConfig
import threading
import cython_control
#from odroid.msg import err
from multiprocessing import Process, Lock

class uav(object):

    def __init__(self, motor_address = None):
        self.simulation = rospy.get_param('simulation')
        print('Initializing node')
        self.uav_name = rospy.get_param('name/uav')
        self.motor_address = np.fromstring(rospy.get_param('/'+self.uav_name+'/port/i2c'), dtype=int,sep=',')
        self._dt = rospy.get_param('controller/dt')
        self.m = rospy.get_param('controller/m')
        # initialization of ROS node
        rospy.init_node(self.uav_name)
        self.tf = tf.TransformerROS(True,rospy.Duration(10.0))
        self.x = np.zeros(3)
        self.v = np.zeros(3)
        self.R = np.eye(3)
        self.R_v = np.eye(3)
        self.W = np.zeros(3)
        b1 = np.array([1,0,0])
        self.xc = np.array([0,0,1])
        self.xc_dot = np.zeros(3)
        self.xc_2dot = np.zeros(3)
        self.xc_3dot = np.zeros(3)
        self.xc_4dot = np.zeros(3)
        self.b1d = np.array([1,0,0])
        self.b1d_dot = np.zeros(3)
        self.b1d_2dot = np.zeros(3)
        self.Rc = np.eye(3)
        self.Wc = np.zeros(3)
        self.Wc_dot = np.zeros(3)
        self.x_c_all = (self.xc, self.xc_dot, self.xc_2dot, self.xc_3dot,self.xc_4dot,
                self.b1d, self.b1d_dot, self.b1d_2dot,
                self.Rc, self.Wc, self.Wc_dot)
        J = np.diag(np.fromstring(rospy.get_param('controller/J'), dtype=float,sep=','))
        #J = np.diag([0.0820, 0.0845, 0.1377])
        e3 = np.array([0.,0.,1.])
        #self.controller = Controller(J,e3)
        #self.controller.m = rospy.get_param('controller/m')
        #self.controller.kx, self.controller.kv = rospy.get_param('controller/gain/pos/kp'), rospy.get_param('controller/gain/pos/kd')
        #self.controller.kR, self.controller.kW = rospy.get_param('controller/gain/att/kp'), rospy.get_param('controller/gain/att/kd')
        #self.controller.kR = rospy.get_param('controller/kR')
        #self.controller.kx = rospy.get_param('controller/kx')
        gains = np.fromstring(rospy.get_param('controller/gains'),dtype=np.double,sep=',')
        self.c_controller = cython_control.c_control(self.m,self._dt,J.flatten(),gains)
        self.F = None
        self.M = None
        l = rospy.get_param('controller/l')
        c_tf = rospy.get_param('controller/c_tf')
        self.A = np.array([[1.,1.,1.,1.],[0,-l,0,l],[l,0,-l,0],[c_tf,-c_tf,c_tf,-c_tf]])
        self.invA = inv(self.A)
        self.R_U2D = np.array([[1.,0,0],[0,-1,0],[0,0,-1]])
        if self.motor_address is not None and not self.simulation:
            self.motor = pyMotor(self.motor_address)
            for k in range(200):
                self.motor.motor_command([60,60,60,60],True)
                time.sleep(0.01)
        #self.ukf = ukf_uav.UnscentedKalmanFilter(12,6,1)
        #self.unscented_kalman_filter()
        self.pub_states = rospy.Publisher('uav_states', states, queue_size=10)
        self.uav_states = states()
        #self.uav_states.xc = self.x_c
        self.tf_subscriber = tf.TransformListener()
        self.dt_vicon = 0.01
        self.v_ave = np.array([0,0,0])
        self.v_ave_ned = self.R_U2D.dot(self.v_ave)
        self.time_vicon = rospy.get_rostime().to_sec()
        self.uav_pose = rospy.Subscriber('/vicon/'+self.uav_name+'/pose',PoseStamped, self.mocap_sub)
        self.trajectory_sub = rospy.Subscriber('/xc', trajectory, self.trajectory_sub)
        self.uav_w = rospy.Subscriber('imu/imu',Imu, self.imu_sub)
        self.v_array = []

        #pub_state = Process(target=self.publish_states)
        #pub_state.start()

        self.lock = threading.Lock()
        #rospy.wait_for_service('/gain_tuning')
        #self.client = dynamic_reconfigure.client.Client('/gain_config', timeout=30, config_callback=self.config_callback)
        #self.odroid_sub = rospy.Subscriber('/drone_variable',error ,self.odroid_callback)
        srv = Server(gainsConfig, self.config_callback)
        rospy.spin()
        #pub_state.join()

    def odroid_callback(self, msg):
        self.odroid_f = msg.force
        self.odroid_Moment = msg.Moment
        pub_cp = rospy.Publisher('moment_c',Point,queue_size=10)
        pub_py = rospy.Publisher('moment_py',Point,queue_size=10)
        pt_c = Point(msg.Moment.x,msg.Moment.y,msg.Moment.z)
        pt_c = Point(msg.throttle[0],msg.throttle[1],msg.throttle[2])
        pt_py = Point(self.c_command[1],self.c_command[2],self.c_command[3])
        pt_py = Point(self.throttle[0],self.throttle[1],self.throttle[2])
        pub_cp.publish(pt_c)
        pub_py.publish(pt_py)

    def config_callback(self, config, level):
        rospy.loginfo('config update')
        #self.controller.kR = config['kR']
        #self.controller.kx = config['kx']
        #self.controller.kv = config['kv']
        #self.controller.kW = config['kW']
        self.c_controller.kx = config['kx']
        self.c_controller.kv = config['kv']
        self.c_controller.kR = config['kR']
        self.c_controller.kW = config['kW']
        self.xc = [config['x'],config['y'],config['z']]
        return config

    def v_update(self):
        if len(self.v_array) < 10:
            self.v_array.append(self.v)
        else:
            self.v_array.pop(0)
            self.v_array.append(self.v)
            self.v_ave = np.mean(self.v_array, axis = 0)

    def trajectory_sub(self, msg):
        self.b1d = msg.b1
        self.xc = msg.xc
        self.xc_dot = msg.xc_dot
        self.xc_2dot = msg.xc_2dot

    def mocap_sub(self, msg):
        try:
            self.dt_vicon = msg.header.stamp.to_sec() - self.time_vicon
            self.time_vicon = msg.header.stamp.to_sec()
            x_v = msg.pose.position
            trans = np.array([x_v.x,x_v.y,x_v.z])

            self.v_q = msg.pose.orientation
            orient = msg.pose.orientation
            self.orientation_v = np.array([orient.x,orient.y,orient.z,orient.w])
            self.euler_angle = tf.transformations.euler_from_quaternion(self.orientation_v)
            euler_angle = (self.euler_angle[0], self.euler_angle[1], self.euler_angle[2])
            self.R_v = self.tf.fromTranslationRotation((0,0,0), self.orientation_v)[:3,:3]
            #(trans,rot) = self.tf_subscriber.lookupTransform('/world', self.uav_name, rospy.Time(0))
            self.v = [(x_c - x_p)/self.dt_vicon for x_c, x_p in zip(trans, self.x)]
            self.v_update()
            self.x = trans
            self.uav_states.x_v = trans.tolist()
            self.uav_states.v_v = self.v_ave.tolist()
            self.uav_states.q_v = self.orientation_v.tolist()
            #self.R_v = self.tf.fromTranslationRotation(trans,rot)[:3,:3]
            self.uav_states.R_v = self.R_v.flatten().tolist()
        except (tf.LookupException, tf.ConnectivityException, tf.ExtrapolationException):
            print('No transform between vicon and UAV found')
        #self.publish_states()

    def camera_sub(self):
        pass

    def imu_sub(self, msg):
        self.imu_w = msg.angular_velocity
        w = msg.angular_velocity

        self.W = np.array([w.x,w.y,w.z])
        self.control()

        acc = msg.linear_acceleration
        self.linear_acceleration = np.array([acc.x,acc.y,acc.z])
        self.linear_velocity = self.linear_acceleration*self._dt
        orient = msg.orientation
        self.orientation = np.array([orient.x,orient.y,orient.z,orient.w])
        self.euler_angle = tf.transformations.euler_from_quaternion(self.orientation)
        euler_angle = (self.euler_angle[0], self.euler_angle[1], self.euler_angle[2]-0.7)
        self.R_imu = self.tf.fromTranslationRotation((0,0,0), self.orientation)[:3,:3]
        self.uav_states.R_imu = self.R_imu.flatten().tolist()
        self.orientation = tf.transformations.quaternion_from_euler(euler_angle[0],euler_angle[1],euler_angle[2])
        self.imu_q = msg.orientation
        self.uav_states.W = self.W.tolist()
        #self.run_ukf()
        #br = tf.TransformBroadcaster()
        #br.sendTransform((0,0,0), (1,0,0,0),
        #        msg.header.stamp,
        #        'imu',
        #        'Jetson')
    def control(self):
        self.x_ned = self.R_U2D.dot(self.x)
        self.v_ave_ned = self.R_U2D.dot(self.v_ave)
        self.R = self.R_U2D.dot(self.R_v.dot(self.R_U2D))
        self.x_c_all = (self.xc, self.xc_dot, self.xc_2dot, self.xc_3dot,self.xc_4dot,
                self.R_U2D.dot(self.b1d), self.b1d_dot, self.b1d_2dot,
                self.Rc, self.Wc, self.Wc_dot)
        #self.F, self.M = self.controller.position_control( self.R, self.W, self.x_ned, self.v_ave_ned, self.x_c_all)
	self.c_command = np.zeros(4)
        self.xc_all = np.array([self.R_U2D.dot(self.xc),self.R_U2D.dot(self.xc_dot),self.xc_2dot,self.xc_3dot,self.xc_4dot,self.b1d,self.b1d_dot,self.b1d_2dot],np.double).flatten()
	self.c_controller.position_control(self.x_ned, self.v_ave_ned,self.R.flatten(),self.W, self.xc_all,self.c_command)

        #command = np.concatenate(([self.F],self.M))
        command = np.dot(self.invA, self.c_command)
        self.uav_states.f_motor = command.tolist()
        command = [val if val > 0 else 0 for val in command]
        command = np.array([val if val < 6 else 6 for val in command])
        self.uav_states.f_motor_sat = command.tolist()
        self.throttle = np.rint(1./0.03*(command+0.37))
        self.uav_states.throttle = self.throttle.tolist()
        motor_on = True
        if self.motor_address is not None and not self.simulation:
            self.uav_states.motor_power = np.array(self.motor.motor_command(self.throttle,True)).flatten().tolist()
            pass


    def motor_command(self, command):
        self.hw_interface.motor_command(command, True)
        pass

    def vector_to_array(self,msg_vec):
        return np.array([msg_vec.x,msg_vec.y,msg_vec.z])

    def quaternion_to_array(self,msg_vec):
        return np.array([msg_vec.x,msg_vec.y,msg_vec.z, msg_vec.w])

    def publish_states(self):
        self.uav_states.x = self.x_ned.tolist()
        self.uav_states.v = self.v_ave_ned.tolist()
        #self.R = self.R_imu
        self.uav_states.R = self.R.flatten().tolist()
        self.uav_states.xc = self.xc
        self.uav_states.xc_ned = self.R_U2D.dot(self.xc).tolist()

        # take only current voltage and rpm from the motor sensor rpm*780/14
        #self.uav_states.b1d = self.c_controller.get_b1d()
        self.uav_states.force = self.c_command[0]
        self.uav_states.moment = self.c_command[1:].tolist()

        Wc_ = np.zeros(3)
        self.c_controller.get_Wc(Wc_)
        self.uav_states.Wc = Wc_

        b1d = np.zeros(3)
        self.c_controller.get_b1d(b1d)
        self.uav_states.b1d = b1d

        eW = np.zeros(3)
        self.c_controller.get_eW(eW)
        self.uav_states.eW = eW

        ev = np.zeros(3)
        self.c_controller.get_ev(ev)
        self.uav_states.ev = ev

        Wc_dot = np.zeros(3)
        self.c_controller.get_Wc_dot(Wc_dot)
        self.uav_states.Wc_dot = Wc_dot
        #self.uav_states.Rc_dot = self.controller.Rc_dot.flatten().tolist()
        #self.uav_states.Rc_2dot = self.controller.Rc_2dot.flatten().tolist()
        self.uav_states.gain_position = [self.c_controller.kx,self.c_controller.kv,0]
        self.uav_states.gain_attitude = [self.c_controller.kR,self.c_controller.kW,0]
        Rc = np.zeros(9)
        self.c_controller.get_Rc(Rc)
        self.uav_states.Rc = Rc

        Rc_dot = np.zeros(9)
        self.c_controller.get_Rc_dot(Rc_dot)
        self.uav_states.Rc_dot = Rc_dot

        Rc_2dot = np.zeros(9)
        self.c_controller.get_Rc_2dot(Rc_2dot)
        self.uav_states.Rc_2dot = Rc_2dot
        ex = np.zeros(3)
        self.c_controller.get_ex(ex)
        self.uav_states.ex = ex

        eR = np.zeros(3)
        self.c_controller.get_eR(eR)
        self.uav_states.eR = eR
        self.uav_states.header.stamp = rospy.get_rostime()
        self.uav_states.header.frame_id = self.uav_name
        self.uav_states.q_imu = self.quaternion_to_array(self.imu_q).tolist()
        self.uav_states.w_imu = self.vector_to_array(self.imu_w).tolist()
        #print(self.uav_states)
        ## TODO add all the states updates
        ## add down-frame desired values
        self.pub_states.publish(self.uav_states)

    def unscented_kalman_filter(self):
        def state_tran(x, dt):
            A = np.eye(12)
            for i in range(3):
                A[i,i+3] = dt
                A[i+6,i+9] = dt
            return np.dot(A,x)
        def obs_state(x):
            A = np.zeros((6,12))
            for i in range(3):
                A[i,i+3] = 1.
                A[i+3, i + 9] = 1.
            return np.dot(A,x)
        q = 0.1
        r = 0.1
        Ns = 12.
        #ukf_test.J = J
        #ukf_test.e3 = e3
        Q = q**3*np.eye(Ns)
        R = r**2
        x = np.zeros(Ns)
        P = np.eye(Ns)
        pts = JulierSigmaPoints(12,5)
        self.ukf_uav = UKF.UnscentedKalmanFilter(dim_x = 12, dim_z =6, dt=self._dt, hx = obs_state, fx = state_tran, points = pts)
        self.ukf_uav.Q = Q
        self.ukf_uav.R = R
        self.ukf_uav.P = P
        pass

    def run_ukf(self):
        self.ukf_uav.predict()
        #Rot = np.reshape(state[6:15],(-1,9))
        #Rot_e = rot_eul(Rot)
        #noise = np.zeros(Ns)
        #noise[:3] = r*(0.5-np.random.random(3))
        #x_obs = np.concatenate((state[:6],np.reshape(Rot_e,(3,)),state[-3:])) # + noise
        #x_obs[:3] += r*(0.5 - np.random.random(3))
        #x_sensor.append(x_obs)
        sens = np.array([self.linear_velocity,self.W]).flatten()
        self.ukf_uav.update(sens)
        #print(self.ukf_uav.x)
        #x = ukf_filter.x
        #x_ukf.append(x)
        br = tf.TransformBroadcaster()
        br.sendTransform( (self.x[0],-self.x[1],-self.x[2]), self.orientation,
                rospy.Time.now(),
                'imu',
                'inertial')
        br.sendTransform((0,0.5,0), (1,0,0,0),
                rospy.Time.now(),
                'inertial',
                'world')
        pass


if __name__ == '__main__':
    print('starting tf_subscriber node')
    uav_test = uav()
