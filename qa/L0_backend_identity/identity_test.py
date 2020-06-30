#!/usr/bin/python

# Copyright (c) 2019-2020, NVIDIA CORPORATION. All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#  * Neither the name of NVIDIA CORPORATION nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS ``AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
# PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY
# OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import argparse
import numpy as np
import os
import sys
from builtins import range
import tritongrpcclient as grpcclient
import tritonhttpclient as httpclient
from tritonclientutils import np_to_triton_dtype

FLAGS = None

if __name__ == '__main__':
   parser = argparse.ArgumentParser()
   parser.add_argument('-v', '--verbose', action="store_true", required=False, default=False,
                       help='Enable verbose output')
   parser.add_argument('-u', '--url', type=str, required=False,
                       help='Inference server URL.')
   parser.add_argument('-i', '--protocol', type=str, required=False, default='http',
                       help='Protocol ("http"/"grpc") used to ' +
                       'communicate with inference service. Default is "http".')

   FLAGS = parser.parse_args()
   if (FLAGS.protocol != "http") and (FLAGS.protocol != "grpc"):
      print("unexpected protocol \"{}\", expects \"http\" or \"grpc\"".format(FLAGS.protocol))
      exit(1)

   client_util = httpclient if FLAGS.protocol == "http" else grpcclient

   if FLAGS.url is None:
      FLAGS.url = "localhost:8000" if FLAGS.protocol == "http" else "localhost:8001"

   # Reuse a single client for all sync tests
   with client_util.InferenceServerClient(FLAGS.url, verbose=FLAGS.verbose) as client:
      for model_name, np_dtype, shape in (
            ("identity_fp32", np.float32, [1,0]),
            ("identity_fp32", np.float32, [1,5]),
            ("identity_uint32", np.uint32, [4,0]),
            ("identity_uint32", np.uint32, [8,5]),
            ("identity_nobatch_int8", np.int8, [0]),
            ("identity_nobatch_int8", np.int8, [7])):
         input_data = (16384 * np.random.randn(*shape)).astype(np_dtype)
         inputs = [client_util.InferInput("INPUT0", input_data.shape, np_to_triton_dtype(input_data.dtype))]
         inputs[0].set_data_from_numpy(input_data)

         results = client.infer(model_name, inputs)
         print(results)

         output_data = results.as_numpy("OUTPUT0")
         if output_data is None:
            print("error: expected 'OUTPUT0'")
            sys.exit(1)

         if not np.array_equal(output_data, input_data):
            print("error: expected output {} to match input {}".format(
               output_data, input_data))
            sys.exit(1)

   # Run async requests to make sure backend handles request batches
   # correctly. We use just HTTP for this since we are not testing the
   # protocol anyway.
   if FLAGS.protocol == "http":
      model_name = "identity_uint32"
      request_parallelism = 4
      shape = [2, 2]
      with client_util.InferenceServerClient(FLAGS.url, concurrency=request_parallelism,
                                             verbose=FLAGS.verbose) as client:
         input_datas = []
         requests = []
         for i in range(request_parallelism):
            input_data = (16384 * np.random.randn(*shape)).astype(np.uint32)
            input_datas.append(input_data)
            inputs = [client_util.InferInput("INPUT0", input_data.shape, np_to_triton_dtype(input_data.dtype))]
            inputs[0].set_data_from_numpy(input_data)
            requests.append(client.async_infer(model_name, inputs))

         for i in range(request_parallelism):
            # Get the result from the initiated asynchronous inference request.
            # Note the call will block till the server responds.
            results = requests[i].get_result()
            print(results)

            output_data = results.as_numpy("OUTPUT0")
            if output_data is None:
               print("error: expected 'OUTPUT0'")
               sys.exit(1)

            if not np.array_equal(output_data, input_datas[i]):
               print("error: expected output {} to match input {}".format(
                  output_data, input_datas[i]))
               sys.exit(1)

         # Make sue the requests ran in parallel.
         statistics = client.get_inference_statistics(model_name)
         print(statistics)
