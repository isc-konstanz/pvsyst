<?php
/*
     All Emoncms code is released under the GNU Affero General Public License.
     See COPYRIGHT.txt and LICENSE.txt.

     ---------------------------------------------------------------------
     Emoncms - open source energy visualisation
     Part of the OpenEnergyMonitor project:
     http://openenergymonitor.org

*/

// no direct access
defined('EMONCMS_EXEC') or die('Restricted access');

class SolarInverter {
    private $log;

    private $redis;
    private $mysqli;

    public $configs;

    public function __construct($mysqli, $redis, $configs) {
        $this->log = new EmonLogger(__FILE__);
        $this->redis = $redis;
        $this->mysqli = $mysqli;
        $this->configs = $configs;
    }

    private function exist($id) {
        if ($this->redis) {
            if ($this->redis->exists("solar:inverter#$id")) {
                return true;
            }
            return false;
        }
        $result = $this->mysqli->query("SELECT id FROM solar_inverter WHERE id = '$id'");
        if ($result->num_rows>0) {
//             if ($this->redis) {
//                 $this->cache($system);
//             }
            return true;
        }
        return false;
    }

    public function create($sysid, $type=null) {
        $sysid = intval($sysid);
        
        if (!empty($type)) {
            $type = preg_replace('/[^\/\|\,\w\s\-\:]/','', $type);
            // TODO: check if inverter exists
        }
        else {
            $type = null;
        }
        
        $stmt = $this->mysqli->prepare("INSERT INTO solar_inverter (sysid,type) VALUES (?,?)");
        $stmt->bind_param("is",$sysid,$type);
        $stmt->execute();
        $stmt->close();
        
        $id = $this->mysqli->insert_id;
        if ($id < 1) {
            throw new SolarException("Unable to create inverter");
        }
        $inverter = array(
            'id' => $id,
            'sysid' => $sysid,
            'type' => $type,
            'count' => 1
        );
        if ($this->redis) {
            $this->add_redis($inverter);
        }
        return $this->parse($inverter);
    }

    public function add_configs($invid, $strid, $configs) {
        $stmt = $this->mysqli->prepare("INSERT INTO solar_inverter_configs (invid,strid,cfgid) VALUES (?,?,?)");
        $stmt->bind_param("iii", $invid, $strid, $configs['id']);
        $stmt->execute();
        $stmt->close();
        
        return array_merge(array(
                'id'=>intval($configs['id']),
                'invid'=>intval($invid),
                'strid'=>intval($strid),
                'count'=>1
                
        ), array_slice($configs, 2));
    }

    public function remove_configs($invid, $cfgid) {
        $configs = $this->get_configs($invid);
        if (count($configs) < 1) {
            return false;
        }
        foreach ($configs as $c) {
            if ($cfgid == $c['id']) {
                $this->mysqli->query("DELETE FROM solar_inverter_configs WHERE `invid` = '$invid' AND `cfgid` = '$cfgid'");
                return true;
            }
        }
        return false;
    }

    private function get_configs($invid) {
        $configs = array();
        
        $results = $this->mysqli->query("SELECT * FROM solar_inverter_configs WHERE invid='$invid'");
        while ($result = $results->fetch_array()) {
            $id = $result['cfgid'];
            $config = $this->configs->get($id);
            $config = array_merge(array(
                'id'=>intval($id),
                'invid'=>intval($invid),
                'strid'=>intval($result['strid']),
                'count'=>intval($result['count'])
                
            ), array_slice($config, 2));
            
            $configs[] = $config;
        }
        usort($configs, function($c1, $c2) {
            if($c1['count'] == $c2['count']) {
                return strcmp($c1['type'], $c2['type']);
            }
            return $c1['count'] - $c2['count'];
        });
        return $configs;
    }

    public function get_list($sysid) {
        if ($this->redis) {
            $inverters =  $this->get_list_redis($sysid);
        } else {
            $inverters =  $this->get_list_mysql($sysid);
        }
        usort($inverters, function($i1, $i2) {
            if($i1['count'] == $i2['count']) {
                return strcmp($i1['type'], $i2['type']);
            }
            return $i1['count'] - $i2['count'];
        });
        return $inverters;
    }

    private function get_list_mysql($sysid) {
        $sysid = intval($sysid);
        
        $inverters = array();
        $results = $this->mysqli->query("SELECT * FROM solar_inverter WHERE sysid='$sysid'");
        while ($result = $results->fetch_array()) {
            $inverter = $this->parse($result);
            $inverters[] = $inverter;
        }
        return $inverters;
    }

    private function get_list_redis($sysid) {
        $inverters = array();
        if ($this->redis->exists("solar:system#$sysid:inverters")) {
            foreach ($this->redis->sMembers("solar:system#$sysid:inverters") as $id) {
                $inverters[] = $this->get_redis($id);
            }
        }
        else {
            $result = $this->mysqli->query("SELECT * FROM solar_inverter WHERE sysid='$sysid'");
            while ($inverter = $result->fetch_array()) {
                $this->add_redis($inverter);
                $inverters[] = $this->parse($inverter);
            }
        }
        return $inverters;
    }

    public function get($id) {
        $id = intval($id);
        if (!$this->exist($id)) {
            throw new SolarException("Inverter for id $id does not exist");
        }
        if ($this->redis) {
            // Get from redis cache
            $inverter = $this->get_redis($id);
        }
        else {
            // Get from mysql db
            $result = $this->mysqli->query("SELECT * FROM solar_inverter WHERE id = '$id'");
            $inverter = $this->parse($result->fetch_array());
        }
        return $inverter;
    }

    private function get_redis($id) {
        return $this->parse((array) $this->redis->hGetAll("solar:inverter#$id"));
    }

    private function add_redis($inverter) {
        $this->redis->sAdd("solar:system#".$inverter['sysid'].":inverters", $inverter['id']);
        $this->redis->hMSet("solar:inverter#".$inverter['id'], $inverter);
    }

    private function parse($inverter, $configs=array()) {
        if ($configs == null) {
            $configs = $this->get_configs($inverter['id']);
        }
        return array(
            'id' => intval($inverter['id']),
            'sysid' => intval($inverter['sysid']),
            'count' => intval($inverter['count']),
            'type' => strval($inverter['type']),
            'configs' => $configs
        );
    }

    public function update($inverter, $fields) {
        $fields = json_decode(stripslashes($fields), true);
        
        if (isset($fields['count'])) {
            $count = $fields['count'];
            
            if (empty($count) || !is_numeric($count) || $count < 1) {
                throw new SolarException("The inverter count is invalid: $count");
            }
            if ($stmt = $this->mysqli->prepare("UPDATE solar_inverter SET count = ? WHERE id = ?")) {
                $stmt->bind_param("ii", $count, $inverter['id']);
                if ($stmt->execute() === false) {
                    $stmt->close();
                    throw new SolarException("Error while update count of inverter#".$inverter['id']);
                }
                $stmt->close();
                
                if ($this->redis) {
                    $this->redis->hset("solar:inverter#".$inverter['id'], 'count', $count);
                }
            }
            else {
                throw new SolarException("Error while setting up database update");
            }
        }
        return array('success'=>true, 'message'=>'Inverter successfully updated');
    }

    public function delete($inverter, $force_delete=false) {
        $result = $this->mysqli->query("SELECT `id` FROM solar_inverter WHERE sysid = '".$inverter['sysid']."'");
        if ($result->num_rows <= 1 && !$force_delete) {
            return array('success'=>false, 'message'=>'Unable to delete last inverter of system.');
        }
        // TODO: verify if configs are not used of any system
        foreach ($this->get_configs($inverter['id']) as $configs) {
            $this->configs->delete($configs['id']);
        }
        $this->mysqli->query("DELETE FROM solar_inverter_configs WHERE `invid` = '".$inverter['id']."'");
        
        $this->mysqli->query("DELETE FROM solar_inverter WHERE `id` = '".$inverter['id']."'");
        if ($this->redis) {
            $this->delete_redis($inverter['id']);
        }
        return array('success'=>true, 'message'=>'Inverter successfully deleted');
    }

    private function delete_redis($id) {
        $sysid = $this->redis->hget("solar:inverter#$id",'sysid');
        $this->redis->del("solar:inverter#$id");
        $this->redis->srem("solar:system#$sysid:inverters", $id);
    }

}
