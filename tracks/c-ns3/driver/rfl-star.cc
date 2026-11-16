// SPDX-License-Identifier: GPL-2.0-only
// Track C driver: RDMA hosts around one switch on the ns-3-alibabacloud model.
//
//   rfl-star <network-file> <output-dir>
//
// It reads one network file that python -m ns3 renders from a scenario, applies every value to the
// model objects, runs the flows, and writes what the model objects hold after setup and what the
// model's own trace sources and counters saw. It never writes a value it did not read back.
// With sample_ns > 0 it also samples the switch queues and every QP's rate at that interval and
// writes a row whenever a value changed. The image build places this file in the scratch directory
// of the SimAI ns-3 tree.

#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/custom-header.h"
#include "ns3/error-model.h"
#include "ns3/int-header.h"
#include "ns3/internet-module.h"
#include "ns3/network-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/qbb-header.h"
#ifdef NS3_MTP
#include "ns3/mtp-interface.h"
#endif

#include <sys/resource.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <filesystem>
#include <fstream>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <vector>

using namespace ns3;

namespace
{

struct Link
{
  uint32_t a, b;
  uint64_t bps, delayNs;
};

struct Flow
{
  uint32_t src, dst;
  uint64_t bytes, startNs;
  uint32_t pg;
};

struct Network
{
  std::map<std::string, std::string> settings;
  std::vector<uint32_t> hosts, switches;
  std::vector<Link> links;
  std::vector<Flow> flows;
};

const std::set<std::string> kSettings = {
    "seed",          "run",           "stop_ns",        "mtu_bytes",       "cc_mode",
    "pfc",           "ecn",           "pause_time_us",  "buffer_bytes",    "kmin_kb",
    "kmax_kb",       "pmax",          "headroom_factor", "pfc_alpha_shift", "l2_chunk_bytes",
    "l2_ack_interval", "window_bytes", "error_rate", "rate_ai_bps", "rate_hai_bps",
    "min_rate_bps",  "ewma_gain",     "rp_timer_us",    "rate_decrease_interval_us",
    "alpha_resume_interval_us", "fast_recovery_times", "clamp_target_rate", "rate_on_first_cnp",
    "sample_ns"};

[[noreturn]] void
Fail (const std::string &why)
{
  std::fprintf (stderr, "rfl-star: %s\n", why.c_str ());
  std::exit (2);
}

Network
Read (const std::string &path)
{
  std::ifstream in (path);
  if (!in)
    Fail ("cannot read " + path);
  Network net;
  std::string line;
  for (int number = 1; std::getline (in, line); ++number)
    {
      line = line.substr (0, line.find ('#'));
      std::istringstream words (line);
      std::string key;
      if (!(words >> key))
        continue;
      bool ok = true;
      if (key == "host" || key == "switch")
        {
          uint32_t id;
          ok = bool (words >> id);
          (key == "host" ? net.hosts : net.switches).push_back (id);
        }
      else if (key == "link")
        {
          Link l;
          ok = bool (words >> l.a >> l.b >> l.bps >> l.delayNs);
          net.links.push_back (l);
        }
      else if (key == "flow")
        {
          Flow f;
          ok = bool (words >> f.src >> f.dst >> f.bytes >> f.startNs >> f.pg);
          net.flows.push_back (f);
        }
      else if (kSettings.count (key))
        {
          std::string value;
          ok = bool (words >> value) && !net.settings.count (key);
          net.settings[key] = value;
        }
      else
        Fail (path + ":" + std::to_string (number) + ": unknown key " + key);
      std::string extra;
      if (!ok || (words >> extra))
        Fail (path + ":" + std::to_string (number) + ": malformed line");
    }
  for (const auto &k : kSettings)
    if (!net.settings.count (k))
      Fail ("missing setting " + k);
  return net;
}

uint64_t
U64 (const Network &net, const std::string &key)
{
  const std::string &text = net.settings.at (key);
  size_t used = 0;
  uint64_t value = 0;
  try
    {
      value = std::stoull (text, &used);
    }
  catch (const std::exception &)
    {
      used = 0;
    }
  if (used != text.size () || text[0] == '-')
    Fail (key + " is not an unsigned integer: " + text);
  return value;
}

double
Real (const Network &net, const std::string &key)
{
  const std::string &text = net.settings.at (key);
  size_t used = 0;
  double value = 0;
  try
    {
      value = std::stod (text, &used);
    }
  catch (const std::exception &)
    {
      used = 0;
    }
  if (used != text.size ())
    Fail (key + " is not a number: " + text);
  return value;
}

// The model reads a host's node id back from its address, (ip >> 8) & 0xffff, in
// QbbNetDevice::Receive and RdmaHw, so host addresses must follow this layout.
Ipv4Address
HostIp (uint32_t id)
{
  return Ipv4Address (0x0b000001 + ((id / 256) << 16) + ((id % 256) << 8));
}

const std::map<uint8_t, const char *> kKinds = {
    {0x11, "data"}, {0xFC, "ack"}, {0xFD, "nack"}, {0xFE, "pfc"}, {0xFF, "cnp"}};

struct Count
{
  uint64_t packets = 0, bytes = 0;
};

struct DeviceCounters
{
  uint32_t node = 0, ifIndex = 0;
  bool isSwitch = false;
  std::map<std::string, Count> tx, rx;
  std::map<std::string, uint64_t> events;
};

std::map<std::pair<uint32_t, uint32_t>, DeviceCounters> g_devices;

// One switch port as the enqueue and dequeue hooks see it.
struct Port
{
  DeviceCounters *c;
  Ptr<SwitchNode> sw;
  Ptr<QbbNetDevice> dev;
};

std::deque<Port> g_ports;

struct FlowPackets
{
  uint64_t dataPackets = 0, dataBytes = 0, nacksReceived = 0;
};

// (host node, source port of the flow) -> what that host's NIC sent and received for it.
std::map<std::pair<uint32_t, uint32_t>, FlowPackets> g_flowPackets;

struct Band
{
  uint64_t dequeued = 0, marked = 0;
};

// (switch node, port, priority, band) -> data packets dequeued and CE-marked there. The band is
// where the MMU's egress bytes stood when SwitchMmu::ShouldSendCN decided.
std::map<std::tuple<uint32_t, uint32_t, uint32_t, std::string>, Band> g_bands;
std::map<std::tuple<uint32_t, uint32_t, uint32_t>, std::pair<uint64_t, int64_t>> g_peaks;
std::vector<std::string> g_marks, g_cnps, g_pfcs;

int64_t
NowNs ()
{
  return Simulator::Now ().GetNanoSeconds ();
}

struct FlowResult
{
  Flow in;
  uint16_t sport, dport;
  bool done = false;
  uint64_t startNs = 0, finishNs = 0, cnpAcks = 0, rxInOrderBytes = 0;
};

std::vector<FlowResult> g_flows;
size_t g_done = 0;

const char *
KindOf (uint8_t protocol)
{
  auto it = kKinds.find (protocol);
  return it == kKinds.end () ? "other" : it->second;
}

void
LogPfc (DeviceCounters *c, const char *direction, const CustomHeader &ch)
{
  std::ostringstream row;
  row << NowNs () << "\t" << c->node << "\t" << c->ifIndex << "\t" << direction << "\t" << unsigned (ch.pfc.qIndex) << "\t" << ch.pfc.time;
  g_pfcs.push_back (row.str ());
}

void
OnTx (DeviceCounters *c, Ptr<const Packet> p)
{
  CustomHeader ch (CustomHeader::L2_Header | CustomHeader::L3_Header | CustomHeader::L4_Header);
  p->PeekHeader (ch);
  Count &n = c->tx[KindOf (ch.l3Prot)];
  n.packets++;
  n.bytes += p->GetSize ();
  if (ch.l3Prot == 0x11 && ch.GetIpv4EcnBits () == 0x03)
    c->events["ce_data_tx"]++;
  if (ch.l3Prot == 0x11 && !c->isSwitch)
    {
      FlowPackets &f = g_flowPackets[{c->node, ch.udp.sport}];
      f.dataPackets++;
      f.dataBytes += p->GetSize ();
    }
  if (ch.l3Prot == 0xFE)
    LogPfc (c, "tx", ch);
}

void
OnRx (DeviceCounters *c, Ptr<const Packet> p)
{
  CustomHeader ch (CustomHeader::L2_Header | CustomHeader::L3_Header | CustomHeader::L4_Header);
  p->PeekHeader (ch);
  Count &n = c->rx[KindOf (ch.l3Prot)];
  n.packets++;
  n.bytes += p->GetSize ();
  if (ch.l3Prot == 0x11 && ch.GetIpv4EcnBits () == 0x03)
    c->events["ce_data_rx"]++;
  if ((ch.l3Prot == 0xFC || ch.l3Prot == 0xFD) && ((ch.ack.flags >> qbbHeader::FLAG_CNP) & 1))
    {
      c->events["cnp_flag_ack_rx"]++;
      if (!c->isSwitch)
        {
          std::ostringstream row;
          row << NowNs () << "\t" << c->node << "\t" << ch.ack.dport;
          g_cnps.push_back (row.str ());
        }
    }
  if (ch.l3Prot == 0xFD && !c->isSwitch)
    g_flowPackets[{c->node, ch.ack.dport}].nacksReceived++;
  if (ch.l3Prot == 0xFE)
    LogPfc (c, "rx", ch);
}

void
OnEnqueue (Port *port, Ptr<const Packet> p, uint32_t qIndex)
{
  CustomHeader ch (CustomHeader::L2_Header | CustomHeader::L3_Header | CustomHeader::L4_Header);
  p->PeekHeader (ch);
  if (ch.l3Prot != 0x11)
    return;
  port->c->events["data_enqueued"]++;
  // QbbNetDevice::SwitchSend fires QbbEnqueue just before it enqueues the packet.
  uint64_t bytes = port->dev->GetQueue ()->GetNBytes (qIndex) + p->GetSize ();
  auto &peak = g_peaks[{port->c->node, port->c->ifIndex, qIndex}];
  if (bytes > peak.first)
    peak = {bytes, NowNs ()};
}

void
OnDequeue (Port *port, Ptr<const Packet> p, uint32_t qIndex)
{
  CustomHeader ch (CustomHeader::L2_Header | CustomHeader::L3_Header | CustomHeader::L4_Header);
  p->PeekHeader (ch);
  if (ch.l3Prot != 0x11)
    return;
  uint32_t ifIndex = port->c->ifIndex;
  // SwitchNode::SwitchNotifyDequeue has removed the packet from the egress account and decided on
  // the mark before QbbDequeue fires, so this is the value ShouldSendCN compared.
  uint64_t egress = port->sw->m_mmu->egress_bytes[ifIndex][qIndex];
  const char *band = egress > port->sw->m_mmu->kmax[ifIndex] ? "above_kmax"
                     : egress > port->sw->m_mmu->kmin[ifIndex] ? "kmin_to_kmax"
                                                                 : "at_most_kmin";
  bool marked = ch.GetIpv4EcnBits () == 0x03;
  Band &b = g_bands[{port->c->node, ifIndex, qIndex, band}];
  b.dequeued++;
  if (marked)
    {
      b.marked++;
      std::ostringstream row;
      row << NowNs () << "\t" << port->c->node << "\t" << ifIndex << "\t" << qIndex << "\t" << egress;
      g_marks.push_back (row.str ());
    }
}

void
OnPhyRxDrop (DeviceCounters *c, Ptr<const Packet>)
{
  c->events["phy_rx_drop"]++;
}

void
OnQbbDrop (DeviceCounters *c, Ptr<const Packet>, uint32_t)
{
  c->events["qbb_drop"]++;
}

void
OnPfc (DeviceCounters *c, uint32_t type)
{
  c->events[type == 1 ? "pause_rx" : "resume_rx"]++;
}

void
OnQpComplete (Ptr<RdmaQueuePair> q)
{
  for (auto &r : g_flows)
    {
      if (r.done || HostIp (r.in.src) != q->sip || r.sport != q->sport)
        continue;
      r.done = true;
      r.startNs = q->startTime.GetNanoSeconds ();
      r.finishNs = Simulator::Now ().GetNanoSeconds ();
      Ptr<RdmaHw> sender = NodeList::GetNode (r.in.src)->GetObject<RdmaDriver> ()->m_rdma;
      r.cnpAcks = sender->qp_cnp[RdmaHw::GetQpKey (q->dip.Get (), q->sport, q->m_pg)];
      Ptr<RdmaHw> receiver = NodeList::GetNode (r.in.dst)->GetObject<RdmaDriver> ()->m_rdma;
      uint64_t rxKey = ((uint64_t) q->sip.Get () << 32) | ((uint64_t) q->m_pg << 16) | q->sport;
      auto rx = receiver->m_rxQpMap.find (rxKey);
      if (rx != receiver->m_rxQpMap.end ())
        r.rxInOrderBytes = rx->second->ReceiverNextExpectedSeq;
      if (++g_done == g_flows.size ())
        Simulator::Stop ();
      return;
    }
}

struct Sampler
{
  uint64_t intervalNs;
  std::FILE *queues, *rates;
  std::map<std::tuple<uint32_t, uint32_t, uint32_t>, uint64_t> lastQueue;
  std::map<size_t, uint64_t> lastRate;
};

void
Sample (Sampler *s)
{
  for (const Port &port : g_ports)
    for (uint32_t q = 0; q < SwitchMmu::qCnt; q++)
      {
        uint64_t bytes = port.dev->GetQueue ()->GetNBytes (q);
        auto key = std::make_tuple (port.c->node, port.c->ifIndex, q);
        auto last = s->lastQueue.find (key);
        if ((last == s->lastQueue.end () && bytes == 0) || (last != s->lastQueue.end () && last->second == bytes))
          continue;
        s->lastQueue[key] = bytes;
        std::fprintf (s->queues, "%lld\t%u\t%u\t%u\t%llu\n", (long long) NowNs (), port.c->node, port.c->ifIndex, q,
                      (unsigned long long) bytes);
      }
  for (size_t i = 0; i < g_flows.size (); i++)
    {
      const FlowResult &r = g_flows[i];
      Ptr<RdmaHw> hw = NodeList::GetNode (r.in.src)->GetObject<RdmaDriver> ()->m_rdma;
      auto qp = hw->m_qpMap.find (RdmaHw::GetQpKey (HostIp (r.in.dst).Get (), r.sport, r.in.pg));
      if (qp == hw->m_qpMap.end ())
        continue;
      uint64_t bps = qp->second->m_rate.GetBitRate ();
      auto last = s->lastRate.find (i);
      if (last != s->lastRate.end () && last->second == bps)
        continue;
      s->lastRate[i] = bps;
      std::fprintf (s->rates, "%lld\t%zu\t%llu\n", (long long) NowNs (), i, (unsigned long long) bps);
    }
  Simulator::Schedule (NanoSeconds (s->intervalNs), &Sample, s);
}

// Pointers and object containers print memory addresses, which differ from run to run.
void
WriteAttributes (std::FILE *out, const std::string &where, Ptr<Object> object)
{
  for (TypeId tid = object->GetInstanceTypeId (); tid != Object::GetTypeId (); tid = tid.GetParent ())
    {
      for (uint32_t i = 0; i < tid.GetAttributeN (); i++)
        {
          TypeId::AttributeInformation info = tid.GetAttribute (i);
          if (!(info.flags & TypeId::ATTR_GET))
            continue;
          std::string type = info.checker->GetValueTypeName ();
          std::string text = "<" + type + ">";
          if (type != "ns3::PointerValue" && type != "ns3::ObjectPtrContainerValue")
            {
              Ptr<AttributeValue> value = info.checker->Create ();
              object->GetAttribute (info.name, *value);
              text = value->SerializeToString (info.checker);
            }
          std::fprintf (out, "%s\t%s\t%s\n", where.c_str (), info.name.c_str (), text.c_str ());
        }
    }
}

std::FILE *
Open (const std::string &dir, const std::string &name, const char *header)
{
  std::FILE *f = std::fopen ((dir + "/" + name).c_str (), "w");
  if (!f)
    Fail ("cannot write " + dir + "/" + name);
  std::fputs (header, f);
  return f;
}

} // namespace

int
main (int argc, char *argv[])
{
  if (argc != 3)
    Fail ("usage: rfl-star <network-file> <output-dir>");
  const Network net = Read (argv[1]);
  const std::string outDir = argv[2];
  std::filesystem::create_directories (outDir);

  const uint32_t nodes = net.hosts.size () + net.switches.size ();
  enum Role { kUnset, kHost, kSwitch };
  std::vector<Role> role (nodes, kUnset);
  for (const auto &[ids, kind] : {std::pair (net.hosts, kHost), std::pair (net.switches, kSwitch)})
    for (uint32_t id : ids)
      {
        if (id >= nodes || role[id] != kUnset)
          Fail ("host ids and switch ids must be 0.." + std::to_string (nodes - 1) + ", each once");
        role[id] = kind;
      }
  if (net.switches.size () != 1 || net.links.size () != net.hosts.size ())
    Fail ("the topology must be one switch with one link to every host");
  const uint32_t sw = net.switches[0];
  std::set<uint32_t> linked;
  for (const Link &l : net.links)
    if (l.a == l.b || l.a >= nodes || l.b >= nodes || role[l.a] != kHost || l.b != sw || !linked.insert (l.a).second ||
        l.bps == 0)
      Fail ("every link is <host> <switch> <bit/s> <delay ns>, one per host");
  for (const Flow &f : net.flows)
    if (f.src >= nodes || f.dst >= nodes || role[f.src] != kHost || role[f.dst] != kHost || f.src == f.dst ||
        f.pg == 0 || f.pg > 7 || f.bytes == 0)
      Fail ("every flow is <src host> <dst host> <bytes> <start ns> <priority 1..7>");
  if (net.flows.empty ())
    Fail ("no flow");

  const uint32_t seed = U64 (net, "seed");
  RngSeedManager::SetSeed (seed);
  RngSeedManager::SetRun (U64 (net, "run"));
  // The HPCC-PINT paths of the model draw from rand(), not from the ns-3 streams.
  std::srand (seed);

  const uint64_t ccMode = U64 (net, "cc_mode");
  const bool pfc = U64 (net, "pfc") != 0, ecn = U64 (net, "ecn") != 0;
  Config::SetDefault ("ns3::QbbNetDevice::PauseTime", UintegerValue (U64 (net, "pause_time_us")));
  Config::SetDefault ("ns3::QbbNetDevice::QbbEnabled", BooleanValue (pfc));
  Config::SetDefault ("ns3::QbbNetDevice::QcnEnabled", BooleanValue (ecn));
  IntHeader::mode = ccMode == 3 ? IntHeader::NORMAL : ccMode == 7 ? IntHeader::TS : ccMode == 10 ? IntHeader::PINT
                                                                                                    : IntHeader::NONE;
  RdmaEgressQueue::ack_q_idx = 3;

  NodeContainer n;
  for (uint32_t id = 0; id < nodes; id++)
    {
      if (role[id] == kHost)
        n.Add (CreateObject<Node> ());
      else
        {
          Ptr<SwitchNode> s = CreateObject<SwitchNode> ();
          s->SetAttribute ("EcnEnabled", BooleanValue (ecn));
          n.Add (s);
        }
    }
  InternetStackHelper internet;
  internet.Install (n);

  const double errorRate = Real (net, "error_rate");
  std::map<uint32_t, uint32_t> switchPort, hostIf;
  std::vector<NetDeviceContainer> linkDevices;
  QbbHelper qbb;
  Ipv4AddressHelper subnets;
  for (size_t i = 0; i < net.links.size (); i++)
    {
      const Link &l = net.links[i];
      qbb.SetDeviceAttribute ("DataRate", DataRateValue (DataRate (l.bps)));
      qbb.SetChannelAttribute ("Delay", TimeValue (NanoSeconds (l.delayNs)));
      if (errorRate > 0)
        {
          Ptr<RateErrorModel> rem = CreateObject<RateErrorModel> ();
          rem->SetAttribute ("ErrorRate", DoubleValue (errorRate));
          rem->SetAttribute ("ErrorUnit", StringValue ("ERROR_UNIT_PACKET"));
          qbb.SetDeviceAttribute ("ReceiveErrorModel", PointerValue (rem));
        }
      NetDeviceContainer d = qbb.Install (n.Get (l.a), n.Get (l.b));
      Ptr<Ipv4> hostIpv4 = n.Get (l.a)->GetObject<Ipv4> ();
      uint32_t ifIndex = hostIpv4->AddInterface (d.Get (0));
      hostIpv4->AddAddress (ifIndex, Ipv4InterfaceAddress (HostIp (l.a), Ipv4Mask (0xff000000)));
      // QbbNetDevice::SendPfc takes the source address of the switch port from Ipv4 interface
      // <ifIndex>, so every switch port needs an address on the same index as its device.
      std::ostringstream base;
      base << "10." << i / 254 + 1 << "." << i % 254 + 1 << ".0";
      subnets.SetBase (base.str ().c_str (), "255.255.255.0");
      subnets.Assign (d);
      switchPort[l.a] = d.Get (1)->GetIfIndex ();
      hostIf[l.a] = ifIndex;
      linkDevices.push_back (d);
      for (uint32_t end = 0; end < 2; end++)
        {
          Ptr<QbbNetDevice> dev = DynamicCast<QbbNetDevice> (d.Get (end));
          DeviceCounters *c = &g_devices[{dev->GetNode ()->GetId (), dev->GetIfIndex ()}];
          c->node = dev->GetNode ()->GetId ();
          c->ifIndex = dev->GetIfIndex ();
          c->isSwitch = end == 1;
          if (c->isSwitch)
            {
              g_ports.push_back ({c, DynamicCast<SwitchNode> (dev->GetNode ()), dev});
              dev->TraceConnectWithoutContext ("QbbEnqueue", MakeBoundCallback (&OnEnqueue, &g_ports.back ()));
              dev->TraceConnectWithoutContext ("QbbDequeue", MakeBoundCallback (&OnDequeue, &g_ports.back ()));
            }
          dev->TraceConnectWithoutContext ("PhyTxBegin", MakeBoundCallback (&OnTx, c));
          dev->TraceConnectWithoutContext ("MacRx", MakeBoundCallback (&OnRx, c));
          dev->TraceConnectWithoutContext ("PhyRxDrop", MakeBoundCallback (&OnPhyRxDrop, c));
          dev->TraceConnectWithoutContext ("QbbDrop", MakeBoundCallback (&OnQbbDrop, c));
          dev->TraceConnectWithoutContext ("QbbPfc", MakeBoundCallback (&OnPfc, c));
        }
    }

  Ptr<SwitchNode> s = DynamicCast<SwitchNode> (n.Get (sw));
  const double headroomFactor = Real (net, "headroom_factor");
  const uint32_t alphaShift = U64 (net, "pfc_alpha_shift");
  for (uint32_t port = 1; port < s->GetNDevices (); port++)
    {
      Ptr<QbbNetDevice> dev = DynamicCast<QbbNetDevice> (s->GetDevice (port));
      uint64_t bps = dev->GetDataRate ().GetBitRate ();
      uint64_t delayNs = DynamicCast<QbbChannel> (dev->GetChannel ())->GetDelay ().GetNanoSeconds ();
      s->m_mmu->ConfigEcn (port, U64 (net, "kmin_kb"), U64 (net, "kmax_kb"), Real (net, "pmax"));
      s->m_mmu->ConfigHdrm (port, uint32_t (headroomFactor * bps * delayNs / 8e9));
      s->m_mmu->pfc_a_shift[port] = alphaShift;
    }
  s->m_mmu->ConfigNPort (s->GetNDevices () - 1);
  s->m_mmu->ConfigBufferSize (U64 (net, "buffer_bytes"));
  s->m_mmu->node_id = sw;
  for (const auto &[host, port] : switchPort)
    {
      Ipv4Address ip = HostIp (host);
      s->AddTableEntry (ip, port);
    }

  const uint64_t mtu = U64 (net, "mtu_bytes");
  for (uint32_t id : net.hosts)
    {
      Ptr<RdmaHw> hw = CreateObject<RdmaHw> ();
      hw->SetAttribute ("CcMode", UintegerValue (ccMode));
      hw->SetAttribute ("Mtu", UintegerValue (mtu));
      hw->SetAttribute ("L2ChunkSize", UintegerValue (U64 (net, "l2_chunk_bytes")));
      hw->SetAttribute ("L2AckInterval", UintegerValue (U64 (net, "l2_ack_interval")));
      hw->SetAttribute ("RateAI", DataRateValue (DataRate (U64 (net, "rate_ai_bps"))));
      hw->SetAttribute ("RateHAI", DataRateValue (DataRate (U64 (net, "rate_hai_bps"))));
      hw->SetAttribute ("MinRate", DataRateValue (DataRate (U64 (net, "min_rate_bps"))));
      hw->SetAttribute ("EwmaGain", DoubleValue (Real (net, "ewma_gain")));
      hw->SetAttribute ("RPTimer", DoubleValue (Real (net, "rp_timer_us")));
      hw->SetAttribute ("RateDecreaseInterval", DoubleValue (Real (net, "rate_decrease_interval_us")));
      hw->SetAttribute ("AlphaResumInterval", DoubleValue (Real (net, "alpha_resume_interval_us")));
      hw->SetAttribute ("FastRecoveryTimes", UintegerValue (U64 (net, "fast_recovery_times")));
      hw->SetAttribute ("ClampTargetRate", BooleanValue (U64 (net, "clamp_target_rate") != 0));
      hw->SetAttribute ("RateOnFirstCnp", DoubleValue (Real (net, "rate_on_first_cnp")));
      Ptr<RdmaDriver> driver = CreateObject<RdmaDriver> ();
      driver->SetNode (n.Get (id));
      driver->SetRdmaHw (hw);
      n.Get (id)->AggregateObject (driver);
      driver->Init ();
      driver->TraceConnectWithoutContext ("QpComplete", MakeCallback (&OnQpComplete));
      for (uint32_t other : net.hosts)
        if (other != id)
          {
            Ipv4Address ip = HostIp (other);
            hw->AddTableEntry (ip, hostIf[id], false);
          }
    }

  std::map<uint32_t, const Link *> linkOf;
  for (const Link &l : net.links)
    linkOf[l.a] = &l;
  uint64_t maxRttNs = 0;
  for (size_t i = 0; i < net.flows.size (); i++)
    {
      const Flow &f = net.flows[i];
      const Link &up = *linkOf[f.src], &down = *linkOf[f.dst];
      uint64_t rttNs = 2 * (up.delayNs + down.delayNs) + mtu * 8000000000ull / up.bps + mtu * 8000000000ull / down.bps;
      maxRttNs = std::max (maxRttNs, rttNs);
      FlowResult r;
      r.in = f;
      r.sport = 10000 + i;
      r.dport = 100;
      g_flows.push_back (r);
      RdmaClientHelper client (f.pg, HostIp (f.src), HostIp (f.dst), r.sport, r.dport, f.bytes,
                               U64 (net, "window_bytes"), rttNs, nullptr, nullptr, i, f.src, f.dst);
      ApplicationContainer app = client.Install (n.Get (f.src));
      app.Start (NanoSeconds (f.startNs));
    }
  s->SetAttribute ("CcMode", UintegerValue (ccMode));
  s->SetAttribute ("MaxRtt", UintegerValue (maxRttNs));

  std::FILE *resolved = Open (outDir, "resolved.tsv", "object\tattribute\tvalue\n");
  std::fprintf (resolved, "global\tRngSeed\t%u\nglobal\tRngRun\t%llu\nglobal\tsrand\t%u\n", RngSeedManager::GetSeed (),
                (unsigned long long) RngSeedManager::GetRun (), seed);
  std::fprintf (resolved, "global\tIntHeader::mode\t%d\nglobal\tRdmaEgressQueue::ack_q_idx\t%u\n", int (IntHeader::mode),
                RdmaEgressQueue::ack_q_idx);
#ifdef NS3_MTP
  // The image builds ns-3 with --enable-mtp. rfl-star never enables it, so one thread runs every event.
  // This call also keeps libmtp in the link: libpoint-to-point uses its lock without linking it.
  std::fprintf (resolved, "global\tMtpInterface::isEnabled\t%d\n", int (MtpInterface::isEnabled ()));
#endif
  for (uint32_t id = 0; id < nodes; id++)
    {
      Ptr<Node> node = n.Get (id);
      std::string where = "node/" + std::to_string (id);
      if (role[id] == kSwitch)
        WriteAttributes (resolved, where + "/switch", node);
      else
        WriteAttributes (resolved, where + "/rdma-hw", node->GetObject<RdmaDriver> ()->m_rdma);
      for (uint32_t i = 1; i < node->GetNDevices (); i++)
        WriteAttributes (resolved, where + "/dev/" + std::to_string (i), node->GetDevice (i));
    }
  for (uint32_t port = 1; port < s->GetNDevices (); port++)
    {
      std::string where = "node/" + std::to_string (sw) + "/mmu/" + std::to_string (port);
      std::fprintf (resolved, "%s\tkmin_bytes\t%u\n%s\tkmax_bytes\t%u\n%s\tpmax\t%g\n", where.c_str (), s->m_mmu->kmin[port],
                    where.c_str (), s->m_mmu->kmax[port], where.c_str (), s->m_mmu->pmax[port]);
      std::fprintf (resolved, "%s\theadroom_bytes\t%u\n%s\tpfc_a_shift\t%u\n", where.c_str (), s->m_mmu->headroom[port],
                    where.c_str (), s->m_mmu->pfc_a_shift[port]);
    }
  std::fprintf (resolved, "node/%u/mmu\tbuffer_bytes\t%u\nnode/%u/mmu\treserve_bytes\t%u\nnode/%u/mmu\tresume_offset_bytes\t%u\n", sw,
                s->m_mmu->buffer_size, sw, s->m_mmu->reserve, sw, s->m_mmu->resume_offset);
  std::fclose (resolved);

  std::FILE *links = Open (outDir, "links.tsv", "link\ta\tb\ta_if\tb_if\ta_bps\tb_bps\tdelay_ns\n");
  for (size_t i = 0; i < linkDevices.size (); i++)
    {
      Ptr<QbbNetDevice> a = DynamicCast<QbbNetDevice> (linkDevices[i].Get (0));
      Ptr<QbbNetDevice> b = DynamicCast<QbbNetDevice> (linkDevices[i].Get (1));
      std::fprintf (links, "%zu\t%u\t%u\t%u\t%u\t%llu\t%llu\t%lld\n", i, a->GetNode ()->GetId (), b->GetNode ()->GetId (),
                    a->GetIfIndex (), b->GetIfIndex (), (unsigned long long) a->GetDataRate ().GetBitRate (),
                    (unsigned long long) b->GetDataRate ().GetBitRate (),
                    (long long) DynamicCast<QbbChannel> (a->GetChannel ())->GetDelay ().GetNanoSeconds ());
    }
  std::fclose (links);

  Sampler sampler{U64 (net, "sample_ns"), Open (outDir, "queues.tsv", "time_ns\tnode\tif\tprio\tbytes\n"),
                  Open (outDir, "rates.tsv", "time_ns\tflow\trate_bps\n"), {}, {}};
  if (sampler.intervalNs > 0)
    Simulator::Schedule (NanoSeconds (0), &Sample, &sampler);
  Simulator::Stop (NanoSeconds (U64 (net, "stop_ns")));
  auto wallStart = std::chrono::steady_clock::now ();
  Simulator::Run ();
  auto wallNs = std::chrono::duration_cast<std::chrono::nanoseconds> (std::chrono::steady_clock::now () - wallStart).count ();
  const int64_t simulatedNs = Simulator::Now ().GetNanoSeconds ();
  // A flow that never completed still has its QP and its receiver's RxQP, so read where each one stopped.
  std::vector<uint64_t> ackedBytes (g_flows.size (), 0);
  for (size_t i = 0; i < g_flows.size (); i++)
    {
      FlowResult &r = g_flows[i];
      if (r.done)
        {
          ackedBytes[i] = r.in.bytes;
          continue;
        }
      Ptr<RdmaHw> sender = NodeList::GetNode (r.in.src)->GetObject<RdmaDriver> ()->m_rdma;
      auto qp = sender->m_qpMap.find (RdmaHw::GetQpKey (HostIp (r.in.dst).Get (), r.sport, r.in.pg));
      if (qp != sender->m_qpMap.end ())
        ackedBytes[i] = qp->second->snd_una;
      Ptr<RdmaHw> receiver = NodeList::GetNode (r.in.dst)->GetObject<RdmaDriver> ()->m_rdma;
      uint64_t rxKey = ((uint64_t) HostIp (r.in.src).Get () << 32) | ((uint64_t) r.in.pg << 16) | r.sport;
      auto rx = receiver->m_rxQpMap.find (rxKey);
      if (rx != receiver->m_rxQpMap.end ())
        r.rxInOrderBytes = rx->second->ReceiverNextExpectedSeq;
    }

  std::FILE *flows = Open (outDir, "flows.tsv",
                           "flow\tsrc\tdst\tsport\tdport\tpg\tbytes\tstart_ns\tcompleted\tfinish_ns\tcnp_acks\trx_in_order_bytes\n");
  for (size_t i = 0; i < g_flows.size (); i++)
    {
      const FlowResult &r = g_flows[i];
      std::fprintf (flows, "%zu\t%u\t%u\t%u\t%u\t%u\t%llu\t%llu\t%d\t%s\t%llu\t%llu\n", i, r.in.src, r.in.dst, r.sport, r.dport,
                    r.in.pg, (unsigned long long) r.in.bytes, (unsigned long long) r.in.startNs, int (r.done),
                    r.done ? std::to_string (r.finishNs).c_str () : "", (unsigned long long) r.cnpAcks,
                    (unsigned long long) r.rxInOrderBytes);
    }
  std::fclose (flows);

  std::FILE *counters = Open (outDir, "counters.tsv", "node\tif\tdirection\tkind\tpackets\tbytes\n");
  std::FILE *events = Open (outDir, "events.tsv", "node\tif\tevent\tcount\n");
  for (const auto &[key, c] : g_devices)
    {
      for (const auto &[kind, count] : c.tx)
        std::fprintf (counters, "%u\t%u\ttx\t%s\t%llu\t%llu\n", key.first, key.second, kind.c_str (),
                      (unsigned long long) count.packets, (unsigned long long) count.bytes);
      for (const auto &[kind, count] : c.rx)
        std::fprintf (counters, "%u\t%u\trx\t%s\t%llu\t%llu\n", key.first, key.second, kind.c_str (),
                      (unsigned long long) count.packets, (unsigned long long) count.bytes);
      for (const auto &[event, count] : c.events)
        std::fprintf (events, "%u\t%u\t%s\t%llu\n", key.first, key.second, event.c_str (), (unsigned long long) count);
    }
  std::fclose (counters);
  std::fclose (events);
  std::fclose (sampler.queues);
  std::fclose (sampler.rates);

  std::FILE *packets = Open (outDir, "flow-packets.tsv", "flow\tdata_packets_sent\tdata_bytes_sent\tnacks_received\tacked_bytes\n");
  for (size_t i = 0; i < g_flows.size (); i++)
    {
      const FlowPackets &f = g_flowPackets[{g_flows[i].in.src, g_flows[i].sport}];
      std::fprintf (packets, "%zu\t%llu\t%llu\t%llu\t%llu\n", i, (unsigned long long) f.dataPackets, (unsigned long long) f.dataBytes,
                    (unsigned long long) f.nacksReceived, (unsigned long long) ackedBytes[i]);
    }
  std::fclose (packets);
  std::FILE *bands = Open (outDir, "ecn-bands.tsv", "node\tif\tprio\tband\tdequeued\tmarked\n");
  for (const auto &[key, b] : g_bands)
    std::fprintf (bands, "%u\t%u\t%u\t%s\t%llu\t%llu\n", std::get<0> (key), std::get<1> (key), std::get<2> (key),
                  std::get<3> (key).c_str (), (unsigned long long) b.dequeued, (unsigned long long) b.marked);
  std::fclose (bands);
  std::FILE *peaks = Open (outDir, "queue-peaks.tsv", "node\tif\tprio\tpeak_bytes\tpeak_time_ns\n");
  for (const auto &[key, peak] : g_peaks)
    std::fprintf (peaks, "%u\t%u\t%u\t%llu\t%lld\n", std::get<0> (key), std::get<1> (key), std::get<2> (key),
                  (unsigned long long) peak.first, (long long) peak.second);
  std::fclose (peaks);
  for (const auto &[name, header, rows] : {std::make_tuple ("marks.tsv", "time_ns\tnode\tif\tprio\tegress_bytes\n", &g_marks),
                                           std::make_tuple ("cnp.tsv", "time_ns\tnode\tsport\n", &g_cnps),
                                           std::make_tuple ("pfc.tsv", "time_ns\tnode\tif\tdirection\tprio\tpause_time\n", &g_pfcs)})
    {
      std::FILE *f = Open (outDir, name, header);
      for (const std::string &row : *rows)
        std::fprintf (f, "%s\n", row.c_str ());
      std::fclose (f);
    }

  struct rusage usage;
  getrusage (RUSAGE_SELF, &usage);
  std::FILE *run = Open (outDir, "run.tsv", "key\tvalue\n");
  std::fprintf (run, "simulated_ns\t%lld\nflows\t%zu\nflows_completed\t%zu\nstopped_by\t%s\n", (long long) simulatedNs,
                g_flows.size (), g_done, g_done == g_flows.size () ? "last-flow-completed" : "stop_ns");
  std::fprintf (run, "simulator_wall_ns\t%lld\nsimulator_max_rss_kib\t%ld\n", (long long) wallNs, usage.ru_maxrss);
  std::fclose (run);
  Simulator::Destroy ();
  return 0;
}
